"""Post operations are simulated; these tests never touch real databases/editors."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from orionis_installer import post_install
from orionis_installer.configuration import set_literal_env
from orionis_installer.exceptions import Cancelled, CompatibilityError, ProcessError
from orionis_installer.messages import MESSAGES
from orionis_installer.models import (
    Database,
    InstallationPlan,
    InstallationResult,
    PostInstallOptions,
    State,
)
from orionis_installer.post_install import connection_ready, run_post_install, seeder_safety
from orionis_installer.prerequisites import Prerequisites


class FakeUI:
    """Simulate post-install input and capture user-facing output without prompting."""

    def __init__(self, *, answers=None, text_answers=None, disallow_input=False):
        """Initialize predetermined answers and isolated UI recordings.

        Parameters
        ----------
        answers : Iterable[bool] or None, optional
            Predetermined confirmation answers consumed in prompt order.
        text_answers : Iterable[str] or None, optional
            Predetermined connection field answers consumed in input order.
        disallow_input : bool, optional
            Whether any prompt must fail the test instead of consuming an answer.
        """
        self.answers = iter(answers or [])
        self.text_answers = iter(text_answers or [])
        self.disallow_input = disallow_input
        self.confirmations = []
        self.entries = []
        self.messages = []
        self.warnings = []

    def confirm(self, label, default):
        """Record a confirmation and return its predetermined answer.

        Parameters
        ----------
        label : str
            Prompt or field label supplied by the post-install operation.
        default : bool
            Fallback answer when no predetermined confirmation remains.

        Returns
        -------
        bool
            Predetermined confirmation or the supplied fallback answer.
        """
        assert not self.disallow_input, "An unexpected prompt requested input."
        self.confirmations.append((label, default))
        return next(self.answers, default)

    def text(self, label, default="", *, validator=None, password=False):
        """Return the next predetermined connection answer without prompting.

        Parameters
        ----------
        label : str
            Prompt or field label supplied by the post-install operation.
        default : str, optional
            Suggested text accepted to match the UI protocol.
        validator : Callable or None, optional
            Optional validation callback accepted by the UI protocol.
        password : bool, optional
            Whether the requested input must be treated as a protected password.

        Returns
        -------
        str
            Next predetermined connection field value.
        """
        assert not self.disallow_input, "An unexpected prompt requested credentials."
        self.entries.append((label, password))
        return next(self.text_answers)

    def message(self, value):
        """Capture a non-secret progress message.

        Parameters
        ----------
        value : str
            Post-install output accepted or recorded by the UI double.
        """
        self.messages.append(value)

    def warning(self, value):
        """Capture a diagnostic warning for assertions.

        Parameters
        ----------
        value : str
            Post-install output accepted or recorded by the UI double.
        """
        self.warnings.append(value)


class FakePostRunner:
    """Record post-install commands and simulate independent failure or cancellation."""

    def __init__(self, *, fail=None, cancel=None):
        """Initialize operation failures, cancellation, and isolated command recordings.

        Parameters
        ----------
        fail : str or None, optional
            Operation name configured to raise the simulated process failure.
        cancel : str or None, optional
            Operation name configured to raise the simulated cancellation.
        """
        self.fail = fail
        self.cancel = cancel
        self.calls = []

    def _operation(self, phase, argv, cwd):
        """Record the requested operation and apply its simulated failure policy.

        Parameters
        ----------
        phase : str
            Requested operation name used for recording and failure selection.
        argv : Sequence[str | Path]
            Command arguments recorded or inspected by the process double.
        cwd : Path
            Working directory supplied to the simulated process or executable lookup.

        Returns
        -------
        SimpleNamespace
            Synthetic process result containing the expected captured output.

        Raises
        ------
        Cancelled or ProcessError
            If the requested phase matches the configured cancellation or failure.
        """
        self.calls.append((phase, [str(argument) for argument in argv], Path(cwd)))
        if self.cancel == phase:
            raise Cancelled("fixture cancellation")
        if self.fail == phase:
            raise ProcessError("The fixture's external operation failed (exit code 9).")
        return SimpleNamespace(stdout="")

    def run(self, argv, *, cwd, timeout):
        """Route a simulated Git or migration command through the operation recorder.

        Parameters
        ----------
        argv : Sequence[str | Path]
            Command arguments recorded or inspected by the process double.
        cwd : Path
            Working directory supplied to the simulated process or executable lookup.
        timeout : float
            Execution timeout accepted to match the real process runner interface.

        Returns
        -------
        SimpleNamespace
            Synthetic process result containing the expected captured output.
        """
        return self._operation("git" if str(argv[1]) == "init" else "migrations", argv, cwd)

    def open_editor(self, launcher, project, *, cwd):
        """Record the trusted editor launch without starting an editor.

        Parameters
        ----------
        launcher : Path
            Trusted editor executable supplied to the simulated launch.
        project : Path
            Final application directory supplied to the simulated editor launch.
        cwd : Path
            Working directory supplied to the simulated process or executable lookup.

        Returns
        -------
        SimpleNamespace
            Synthetic result from the recorded editor operation.
        """
        return self._operation("editor", [launcher, "--new-window", project], cwd)


@pytest.fixture
def ready_project(tmp_path, monkeypatch):
    """Create a verified synthetic application and stub its trusted editor lookup.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    monkeypatch : pytest.MonkeyPatch
        Fixture replacing process state or external dependencies for the test.

    Returns
    -------
    tuple[InstallationResult, Prerequisites]
        Completed synthetic result and trusted executable prerequisites.
    """
    root = tmp_path / "project Ω with spaces"
    root.mkdir()
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("fixture interpreter; never executed")
    (root / ".env").write_text("DB_CONNECTION='sqlite'\nDB_DATABASE='database/database.sqlite'\n")
    (root / "database" / "seeders").mkdir(parents=True)
    (root / "database" / "seeders" / "safe_fixture.py").write_text(
        "# Fixture without users or credentials.\n"
    )

    def trusted_editor(name, cwd, *, excluded_roots):
        """Verify the editor lookup excludes the application and return a trusted executable.

        Parameters
        ----------
        name : str
            Editor command name requested by post-install code.
        cwd : Path
            Working directory supplied to the simulated process or executable lookup.
        excluded_roots : tuple[Path, ...]
            Application roots that the executable resolver must exclude.

        Returns
        -------
        Path
            Trusted host interpreter used as the offline editor executable.
        """
        assert name == "code"
        assert cwd == root and excluded_roots == (root,)
        return Path(sys.executable)

    monkeypatch.setattr(post_install, "resolve_executable", trusted_editor)
    plan = InstallationPlan("app", root)
    result = InstallationResult(plan, creation=State.COMPLETED, published=True)
    tools = Prerequisites(
        Path(sys.executable), Path(sys.executable), Path(sys.executable), "3.14.6"
    )
    return result, tools


def execute(result, tools, runner, ui, options=None, *, no_interaction=True):
    """Run simulated post-install operations with the supplied choices and interaction policy.

    Parameters
    ----------
    result : InstallationResult
        Synthetic published application whose post-install states are recorded.
    tools : Prerequisites
        Synthetic executable prerequisites used by the post-install runner.
    runner : FakePostRunner
        Process double recording calls and simulating failure or cancellation.
    ui : FakeUI
        Interaction double recording messages and supplying predetermined answers.
    options : PostInstallOptions or None
        Explicit follow-up choices, or None to leave every choice unset.
    no_interaction : bool
        Whether unset choices must be skipped without requesting input.
    """
    run_post_install(
        result, options or PostInstallOptions(), tools, runner, ui, no_interaction=no_interaction
    )


@pytest.mark.parametrize(
    "options,no_interaction",
    [
        (PostInstallOptions(), True),
        (PostInstallOptions(False, False, False), True),
        (PostInstallOptions(False, False, False), False),
    ],
)
def test_unspecified_nointeraction_or_negative_flags_never_prompt(
    ready_project, options, no_interaction
):
    """Verify that unset non-interactive choices and negative flags never prompt.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    options : PostInstallOptions or None
        Explicit follow-up choices, or None to leave every choice unset.
    no_interaction : bool
        Whether unset choices must be skipped without requesting input.
    """
    result, tools = ready_project
    runner = FakePostRunner()
    execute(
        result, tools, runner, FakeUI(disallow_input=True), options, no_interaction=no_interaction
    )
    assert (result.git, result.migrations, result.editor) == (State.SKIPPED,) * 3
    assert result.exit_code == 0 and not runner.calls


def test_interactive_questions_are_git_then_migrations_then_editor(ready_project):
    """Verify the confirmation order of Git, migrations, and editor operations.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    """
    result, tools = ready_project
    ui = FakeUI(answers=[True, False, True])
    runner = FakePostRunner()
    execute(result, tools, runner, ui, no_interaction=False)
    assert [default for _, default in ui.confirmations] == [True, False, True]
    assert "Git" in ui.confirmations[0][0]
    assert ui.confirmations[1][0] == MESSAGES["post_migration_prompt"]
    assert "Visual Studio Code" in ui.confirmations[2][0]
    assert [phase for phase, _, _ in runner.calls] == ["git", "editor"]
    assert (result.git, result.migrations, result.editor) == (
        State.COMPLETED,
        State.SKIPPED,
        State.COMPLETED,
    )


@pytest.mark.parametrize("failure", ["git", "migrations", "editor"])
def test_independent_post_operations_continue_after_failure(ready_project, failure):
    """Verify that independent post operations continue after failure.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    failure : str
        Follow-up operation configured to fail in the process double.
    """
    result, tools = ready_project
    runner = FakePostRunner(fail=failure)
    ui = FakeUI(disallow_input=True)
    execute(result, tools, runner, ui, PostInstallOptions(True, True, True))
    assert [phase for phase, _, _ in runner.calls] == ["git", "migrations", "editor"]
    assert getattr(result, failure) == State.FAILED
    assert result.creation == State.COMPLETED and result.exit_code == 3
    assert result.plan.path.exists() and len(result.warnings) == 1
    assert ui.warnings == result.warnings
    if failure == "migrations":
        assert MESSAGES["migration_partial_warning"] in result.warnings[0]
        assert not any("migrate:fresh" in argv for _, argv, _ in runner.calls)


@pytest.mark.parametrize("phase", ["git", "migrations", "editor"])
def test_cancellation_stops_remaining_post_operations(ready_project, phase):
    """Verify that cancellation stops remaining post operations.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    phase : str
        Follow-up operation configured to cancel in the process double.
    """
    result, tools = ready_project
    runner = FakePostRunner(cancel=phase)
    with pytest.raises(Cancelled) as raised:
        execute(
            result, tools, runner, FakeUI(disallow_input=True), PostInstallOptions(True, True, True)
        )
    assert raised.value.exit_code == 130
    assert getattr(result, phase) == State.CANCELLED
    assert runner.calls[-1][0] == phase
    assert result.plan.path.exists()


def test_cancelled_prompt_is_not_a_negative_answer(ready_project):
    """Verify cancellation propagation when the user interrupts a confirmation prompt.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    """
    result, tools = ready_project

    class CancelledUI(FakeUI):
        """Simulate a user interrupt at the first confirmation prompt."""

        def confirm(self, label, default):
            """Interrupt the confirmation to simulate user cancellation.

            Parameters
            ----------
            label : str
                Prompt or field label supplied by the post-install operation.
            default : bool
                Fallback answer when no predetermined confirmation remains.

            Raises
            ------
            KeyboardInterrupt
                Always, to simulate a cancelled user prompt.
            """
            raise KeyboardInterrupt

    runner = FakePostRunner()
    with pytest.raises(Cancelled):
        execute(result, tools, runner, CancelledUI(), no_interaction=False)
    assert result.git == State.CANCELLED
    assert result.migrations == State.PENDING and result.editor == State.PENDING
    assert not runner.calls


@pytest.mark.parametrize(
    "expression", ["'password123'", "Hash.make('secret')", "Env.get('ADMIN_PASSWORD')"]
)
def test_unsafe_admin_seeder_refused_before_any_migration_or_credential_mutation(
    ready_project, expression
):
    """Verify that unsafe administrative seeders fail before migrations or credential edits.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    expression : str
        Administrative password expression that must trigger the seeder guard.
    """
    result, tools = ready_project
    seeder = result.plan.path / "database" / "seeders" / "admin.py"
    seeder.write_text(f"administrator = {{'email':'admin@example.com','password':{expression}}}\n")
    environment_before = (result.plan.path / ".env").read_bytes()
    runner = FakePostRunner()
    ui = FakeUI(disallow_input=True)
    execute(result, tools, runner, ui, PostInstallOptions(False, True, True))
    assert result.migrations == State.FAILED and result.editor == State.COMPLETED
    assert [phase for phase, _, _ in runner.calls] == ["editor"]
    assert (result.plan.path / ".env").read_bytes() == environment_before
    assert result.warnings[0] == MESSAGES["seeders_unsafe"]
    assert "password123" not in str(ui.warnings) and "secret" not in str(ui.warnings)


def test_invalid_seeder_syntax_refuses_execution(ready_project):
    """Verify rejection of invalid seeder syntax before process execution.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    """
    result, _tools = ready_project
    (result.plan.path / "database" / "seeders" / "bad.py").write_text("def broken(\n")
    with pytest.raises(CompatibilityError):
        seeder_safety(result.plan.path)


def test_git_init_does_not_add_commit_remote_or_configure_global(ready_project):
    """Verify that Git initialization performs only the authorized local init command.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    """
    result, tools = ready_project
    runner = FakePostRunner()
    execute(
        result, tools, runner, FakeUI(disallow_input=True), PostInstallOptions(True, False, False)
    )
    assert runner.calls == [("git", [str(tools.git), "init"], result.plan.path)]


def test_migration_uses_only_final_project_python_and_exact_command(ready_project):
    """Verify the exact migration command uses only the final application interpreter.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    """
    result, tools = ready_project
    runner = FakePostRunner()
    ui = FakeUI(disallow_input=True)
    execute(result, tools, runner, ui, PostInstallOptions(False, True, False))
    phase, argv, cwd = runner.calls[0]
    assert phase == "migrations" and cwd == result.plan.path
    assert Path(argv[0]).is_relative_to(result.plan.path / ".venv")
    assert argv[1:] == ["-B", "reactor", "migrate", "--seed"]
    assert result.migrations == State.COMPLETED
    assert "sqlite" in ui.messages[0] and MESSAGES["migration_data_warning"] in ui.messages[0]


def test_missing_editor_is_warning_and_application_is_preserved(ready_project, monkeypatch):
    """Verify that a missing editor produces a warning and preserves the application.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    monkeypatch : pytest.MonkeyPatch
        Fixture replacing process state or external dependencies for the test.
    """
    result, tools = ready_project
    monkeypatch.setattr(post_install, "resolve_executable", lambda *args, **kwargs: None)
    execute(
        result,
        tools,
        FakePostRunner(),
        FakeUI(disallow_input=True),
        PostInstallOptions(False, False, True),
    )
    assert result.editor == State.FAILED and result.exit_code == 3
    assert result.warnings[0] == MESSAGES["editor_missing"] and result.plan.path.exists()


def external_project(ready_project):
    """Adapt the synthetic application to an incomplete MySQL connection.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.

    Returns
    -------
    tuple[InstallationResult, Prerequisites]
        Synthetic MySQL application result and unchanged executable prerequisites.
    """
    original, tools = ready_project
    result = InstallationResult(
        InstallationPlan("app", original.plan.path, database=Database.MYSQL),
        creation=State.COMPLETED,
        published=True,
    )
    (result.plan.path / ".env").write_text("DB_CONNECTION='mysql'\nDB_PORT=3306\n")
    return result, tools


def test_noninteractive_migration_requires_complete_external_connection_without_prompt(
    ready_project,
):
    """Verify that incomplete external credentials fail without non-interactive prompts.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    """
    result, tools = external_project(ready_project)
    runner = FakePostRunner()
    execute(
        result, tools, runner, FakeUI(disallow_input=True), PostInstallOptions(False, True, False)
    )
    assert result.migrations == State.FAILED and result.exit_code == 3
    assert not runner.calls


def test_connection_display_never_contains_password_or_credential_urls(ready_project):
    """Verify exclusion of passwords and credential-bearing URLs from connection output.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    """
    result, tools = external_project(ready_project)
    root = result.plan.path
    for key, value in {
        "DB_HOST": "mysql://private-user:host-password@example.com",
        "DB_DATABASE": "production",
        "DB_USERNAME": "private-user",
        "DB_PASSWORD": "private-password",
    }.items():
        set_literal_env(root / ".env", key, value)
    ui = FakeUI(disallow_input=True)
    execute(result, tools, FakePostRunner(), ui, PostInstallOptions(False, True, False))
    assert result.migrations == State.COMPLETED
    display = " ".join(ui.messages + ui.warnings)
    assert "production" in display
    assert all(
        secret not in display for secret in ["private-user", "host-password", "private-password"]
    )


def test_interactive_missing_credentials_allow_postponing(ready_project):
    """Verify that the user can defer migration when external credentials are missing.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    """
    result, tools = external_project(ready_project)
    runner = FakePostRunner()
    execute(
        result,
        tools,
        runner,
        FakeUI(answers=[False]),
        PostInstallOptions(False, True, False),
        no_interaction=False,
    )
    assert result.migrations == State.SKIPPED and not runner.calls


def test_interactive_credentials_use_protected_password_and_verified_keys(ready_project):
    """Verify protected password input and persistence through the inspected connection keys.

    Parameters
    ----------
    ready_project : tuple[InstallationResult, Prerequisites]
        Verified synthetic application result and executable prerequisites.
    """
    result, tools = external_project(ready_project)
    ui = FakeUI(
        answers=[True],
        text_answers=["localhost", "3306", "fixture-db", "fixture-user", "private-password"],
    )
    execute(
        result,
        tools,
        FakePostRunner(),
        ui,
        PostInstallOptions(False, True, False),
        no_interaction=False,
    )
    assert result.migrations == State.COMPLETED
    assert ui.entries[-1] == (MESSAGES["connection_password_label"], True)
    assert all(not protected for _, protected in ui.entries[:-1])
    assert "private-password" not in " ".join(ui.messages + ui.warnings)


@pytest.mark.parametrize("database", ["", "../outside.sqlite", "/outside.sqlite"])
def test_sqlite_connection_must_remain_local(database):
    """Verify rejection of missing or non-local SQLite connection paths.

    Parameters
    ----------
    database : str
        Missing or non-local SQLite filename that must fail readiness.
    """
    assert not connection_ready({"DB_CONNECTION": "sqlite", "DB_DATABASE": database})
