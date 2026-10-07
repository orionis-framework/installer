"""Test command contracts with controlled installation and terminal fixtures."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from orionis_installer import __version__, cli
from orionis_installer.exceptions import (
    CompatibilityError,
    PrerequisiteError,
    ProcessError,
)
from orionis_installer.models import (
    DEFAULT_DESCRIPTION,
    DEFAULT_STACK,
    STACKS,
    Database,
    InstallationResult,
    Stack,
    State,
    Storage,
)
from orionis_installer.ui.messages import MESSAGES

CLI_RUNNER = CliRunner()


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Replace command dependencies with a recorder for plans and consent.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture replacing command or terminal dependencies for this case.
    tmp_path : Path
        Temporary parent directory reserved for this test project.

    Returns
    -------
    SimpleNamespace
        Captured command state and ordered interaction events.
    """
    monkeypatch.chdir(tmp_path)
    events = []
    captured = SimpleNamespace(
        events=events, plan=None, options=None, no_interaction=None, result=None
    )

    class FakeUI:
        """Record command interaction without opening a real terminal."""

        def __init__(self, **options):
            """Capture terminal preferences supplied by the command.

            Parameters
            ----------
            **options : dict
                Terminal preferences supplied to the fake UI constructor.
            """
            captured.ui_options = options

        def require_tty(self):
            """Record the terminal requirement without querying real streams."""
            events.append("tty")

        def banner(self):
            """Record banner rendering after prerequisite verification."""
            events.append("banner")

        def section(self, label, detail=""):
            """Record a grouped wizard heading.

            Parameters
            ----------
            label : str
                Heading for the current group of questions.
            detail : str, optional
                Supporting instruction displayed below the heading.
            """
            events.append(("section", label, detail))

        @contextmanager
        def progress(self):
            """Record progress boundaries and supply the phase callback.

            Yields
            ------
            SimpleNamespace
                Recorder receiving installation phase updates.
            """
            events.append("progress_start")
            try:
                yield SimpleNamespace(step=self.message)
            finally:
                events.append("progress_end")

        def text(self, label, default="", validator=None, password=False):
            """Record a text question and return its initial value.

            Parameters
            ----------
            label : str
                Question text recorded for prompt-order assertions.
            default : str or bool
                Initial text, machine value, or consent returned by the fake prompt.
            validator : Callable or None
                Input validator accepted without execution by this recorder.
            password : bool
                Masking preference supplied with the text prompt.

            Returns
            -------
            str
                Initial text supplied by the command.
            """
            events.append(("text", label))
            return default

        def select(self, label, choices, default, *, descriptions=None):
            """Record a selector question and return its default machine value.

            Parameters
            ----------
            label : str
                Question text recorded for prompt-order assertions.
            choices : list of tuple of str
                Machine values and captions supplied by the command.
            default : str or bool
                Initial text, machine value, or consent returned by the fake prompt.
            descriptions : dict[str, str] or None, optional
                Supporting copy for each stack selection.

            Returns
            -------
            str
                Initial machine value supplied by the command.
            """
            events.append(("select", label))
            return default

        def confirm(self, label, default):
            """Record a consent question and return the proposed decision.

            Parameters
            ----------
            label : str
                Question text recorded for prompt-order assertions.
            default : str or bool
                Initial text, machine value, or consent returned by the fake prompt.

            Returns
            -------
            bool
                Consent default supplied by the command.
            """
            events.append(("confirm", label))
            return default

        def summary(self, plan):
            """Record plan presentation before installation.

            Parameters
            ----------
            plan : InstallationPlan
                Validated application choices supplied to the simulated phase.
            """
            events.append("summary")

        def message(self, message):
            """Record informational text for phase assertions.

            Parameters
            ----------
            message : str
                Diagnostic text recorded without terminal rendering.
            """
            events.append(("message", message))

        def warning(self, message):
            """Record warnings for cancellation and recovery assertions.

            Parameters
            ----------
            message : str
                Diagnostic text recorded without terminal rendering.
            """
            events.append(("warning", message))

        def error(self, message):
            """Record failure diagnostics for exit-code assertions.

            Parameters
            ----------
            message : str
                Diagnostic text recorded without terminal rendering.
            """
            events.append(("error", message))

        def final(self, result):
            """Capture the completed result before command termination.

            Parameters
            ----------
            result : InstallationResult
                Application result captured or updated by the simulated phase.
            """
            captured.result = result
            events.append("final")

    def prerequisites(process_runner, *, cwd, announce):
        """Record prerequisite verification and return an inert tool bundle.

        Parameters
        ----------
        process_runner : Runner
            Process dependency accepted without launching external tools.
        cwd : Path
            Explicit working directory supplied for prerequisite discovery.
        announce : Callable
            Phase callback accepted without invoking real terminal output.

        Returns
        -------
        SimpleNamespace
            Empty tool bundle for the simulated installer.
        """
        events.append("prerequisites")
        return SimpleNamespace()

    class FakeInstaller:
        """Return verified creation results without touching a real project."""

        def __init__(self, plan, prerequisites, process_runner, *, on_step):
            """Capture the plan selected by the command.

            Parameters
            ----------
            plan : InstallationPlan
                Validated application choices supplied to the simulated phase.
            prerequisites : object
                Inert tool bundle accepted without resolving executables.
            process_runner : Runner
                Process dependency accepted without launching external tools.
            on_step : Callable
                Installer callback accepted without invoking external operations.
            """
            captured.plan = plan

        def install(self):
            """Return a published result without executing external tools.

            Returns
            -------
            InstallationResult
                Published result with completed creation state.
            """
            events.append("install")
            return InstallationResult(captured.plan, creation=State.COMPLETED, published=True)

    def post(result, options, prerequisites, process_runner, ui, *, no_interaction):
        """Capture consent options and mark simulated post operations as skipped.

        Parameters
        ----------
        result : InstallationResult
            Application result captured or updated by the simulated phase.
        options : PostInstallOptions
            Tri-state consent options supplied by the command.
        prerequisites : object
            Inert tool bundle accepted without resolving executables.
        process_runner : Runner
            Process dependency accepted without launching external tools.
        ui : object
            Fake UI accepted without requesting post-install input.
        no_interaction : bool
            Preference captured for post-install consent assertions.
        """
        events.append("post")
        captured.options = options
        captured.no_interaction = no_interaction
        result.git = result.migrations = result.editor = State.SKIPPED

    monkeypatch.setattr(cli, "UI", FakeUI)
    monkeypatch.setattr(cli, "check_prerequisites", prerequisites)
    monkeypatch.setattr(cli, "Installer", FakeInstaller)
    monkeypatch.setattr(cli, "run_post_install", post)
    return captured


@pytest.mark.parametrize("arguments", [["--help"], ["new", "--help"], ["--version"]])
def test_help_and_version_need_no_tty_or_prerequisites(monkeypatch, arguments):
    """Keep help and version independent of terminals and prerequisite checks.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture replacing command or terminal dependencies for this case.
    arguments : list of str
        CLI arguments selecting the behavior checked by this case.
    """

    def forbidden(*args, **kwargs):
        """Fail if help or version unexpectedly checks prerequisites.

        Parameters
        ----------
        *args : tuple
            Positional arguments accepted by the substituted phase callback.
        **kwargs : dict
            Keyword arguments accepted by the substituted phase callback.

        Raises
        ------
        pytest.fail.Exception
            If help or version unexpectedly reaches prerequisite verification.
        """
        pytest.fail("Help and version must not check prerequisites.")

    monkeypatch.setattr(cli, "check_prerequisites", forbidden)
    result = CLI_RUNNER.invoke(cli.app, arguments)
    assert result.exit_code == 0, result.output
    if arguments == ["--version"]:
        assert result.output.strip() == f"orionis-installer {__version__}"
    else:
        assert "--no-color" in result.output


def test_no_interaction_has_safe_defaults_and_zero_prompts(harness, tmp_path):
    """Apply noninteractive defaults without prompting or requiring a terminal.

    Parameters
    ----------
    harness : SimpleNamespace
        Command fixture recording plans, consent, results, and phase events.
    tmp_path : Path
        Temporary parent directory reserved for this test project.
    """
    result = CLI_RUNNER.invoke(cli.app, ["new", "--no-interaction"])
    assert result.exit_code == 0, result.exception
    assert harness.plan.name == "orionis-app"
    assert harness.plan.path == tmp_path / "orionis-app"
    assert harness.plan.description == DEFAULT_DESCRIPTION
    assert harness.plan.author_name is None and harness.plan.author_email is None
    assert harness.plan.storage == Storage.LOCAL and harness.plan.database == Database.SQLITE
    assert harness.plan.stack == DEFAULT_STACK
    assert harness.plan.extras == ("factories",)
    assert harness.options.git is harness.options.migrate is harness.options.open is None
    assert harness.no_interaction is True
    assert not any(
        isinstance(item, tuple) and item[0] in {"text", "select", "confirm"}
        for item in harness.events
    )
    assert "tty" not in harness.events
    assert harness.events.index("prerequisites") < harness.events.index("banner")


def test_explicit_interactive_fields_skip_their_prompts(harness):
    """Honor explicit project fields and negative post-install flags.

    Parameters
    ----------
    harness : SimpleNamespace
        Command fixture recording plans, consent, results, and phase events.
    """
    result = CLI_RUNNER.invoke(
        cli.app,
        [
            "new",
            "blog",
            "--stack",
            "blank",
            "--description",
            "My application",
            "--author-name",
            "Zoë",
            "--author-email",
            "maria@example.com",
            "--storage",
            "s3",
            "--database",
            "redshift",
            "--no-git",
            "--no-migrate",
            "--no-open",
        ],
    )
    assert result.exit_code == 0, result.exception
    assert not any(
        isinstance(item, tuple) and item[0] in {"text", "select"} for item in harness.events
    )
    assert ("confirm", MESSAGES["confirm"]) in harness.events
    assert harness.plan.extras == ("factories", "redshift", "s3")
    assert harness.options.git is harness.options.migrate is harness.options.open is False


def test_interactive_questions_have_the_specified_order(harness):
    """Ask project questions in the required sequence after prerequisites.

    Parameters
    ----------
    harness : SimpleNamespace
        Command fixture recording plans, consent, results, and phase events.
    """
    result = CLI_RUNNER.invoke(cli.app, ["new"])
    assert result.exit_code == 0, result.exception
    prompts = [
        item[1]
        for item in harness.events
        if isinstance(item, tuple) and item[0] in {"text", "select", "confirm"}
    ]
    assert prompts == [
        MESSAGES[key]
        for key in (
            "stack",
            "name",
            "description",
            "author_name",
            "author_email",
            "storage",
            "database",
            "confirm",
        )
    ]
    assert harness.events.index("prerequisites") < harness.events.index("banner")


@pytest.mark.parametrize("stack", ["blank", "ssr", "Blank", "SSR"])
def test_explicit_stack_selects_catalog_source_without_stack_prompt(harness, stack):
    """Resolve explicit stacks to the configured repository and exact branch.

    Parameters
    ----------
    harness : SimpleNamespace
        Recorder capturing the CLI's resolved plan and interaction.
    stack : str
        Lowercase or display-case stack supplied to the public command.
    """
    result = CLI_RUNNER.invoke(cli.app, ["new", "--stack", stack, "--no-interaction"])
    assert result.exit_code == 0, result.exception
    selection = Stack(stack.lower())
    assert harness.plan.stack == selection
    assert harness.plan.source == STACKS[selection]
    assert harness.plan.source.branch == f"{selection.value}_1.x"
    assert ("select", MESSAGES["stack"]) not in harness.events
    assert harness.events.index("progress_start") < harness.events.index("install")
    assert harness.events.index("install") < harness.events.index("progress_end")


def test_unknown_stack_is_rejected_before_installation(harness):
    """Reject an unconfigured stack before any prerequisite or project mutation.

    Parameters
    ----------
    harness : SimpleNamespace
        Recorder detecting installation and prerequisite attempts.
    """
    result = CLI_RUNNER.invoke(cli.app, ["new", "--stack", "unknown", "--no-interaction"])
    assert result.exit_code == 2
    assert "prerequisites" not in harness.events and "install" not in harness.events


def test_all_non_interactive_uses_concrete_defaults(harness):
    """Separate aggregate driver installation from concrete active defaults.

    Parameters
    ----------
    harness : SimpleNamespace
        Command fixture recording plans, consent, results, and phase events.
    """
    result = CLI_RUNNER.invoke(
        cli.app, ["new", "--storage", "all", "--database", "all", "--no-interaction"]
    )
    assert result.exit_code == 0, result.exception
    assert harness.plan.active_storage == Storage.LOCAL
    assert harness.plan.active_database == Database.SQLITE
    assert harness.plan.extras == ("database", "factories", "storage")


def test_all_explicit_defaults_and_final_path(harness, tmp_path):
    """Preserve explicit active drivers and a Unicode final destination.

    Parameters
    ----------
    harness : SimpleNamespace
        Command fixture recording plans, consent, results, and phase events.
    tmp_path : Path
        Temporary parent directory reserved for this test project.
    """
    destination = tmp_path / "Application café with spaces"
    result = CLI_RUNNER.invoke(
        cli.app,
        [
            "new",
            "blog",
            "--path",
            str(destination),
            "--storage",
            "all",
            "--default-storage",
            "gcs",
            "--database",
            "all",
            "--default-database",
            "redshift",
            "--no-interaction",
        ],
    )
    assert result.exit_code == 0, result.exception
    assert harness.plan.path == destination
    assert harness.plan.active_storage == Storage.GCS
    assert harness.plan.active_database == Database.REDSHIFT


@pytest.mark.parametrize(
    ("flag", "attribute"), [("--git", "git"), ("--migrate", "migrate"), ("--open", "open")]
)
def test_positive_post_flags_preserve_explicit_consent(harness, flag, attribute):
    """Forward positive post-install flags as explicit consent.

    Parameters
    ----------
    harness : SimpleNamespace
        Command fixture recording plans, consent, results, and phase events.
    flag : str
        Positive post-install option under test.
    attribute : str
        Consent field corresponding to the selected positive option.
    """
    result = CLI_RUNNER.invoke(cli.app, ["new", "--no-interaction", flag])
    assert result.exit_code == 0, result.exception
    assert getattr(harness.options, attribute) is True


@pytest.mark.parametrize(
    "arguments",
    [
        ["new", "bad/name", "--no-interaction"],
        ["new", "--author-email", "invalid", "--no-interaction"],
        ["new", "--storage", "s3", "--default-storage", "local", "--no-interaction"],
        ["new", "--database", "all", "--default-database", "all", "--no-interaction"],
    ],
)
def test_invalid_plans_return_two_without_install(harness, arguments):
    """Reject invalid plans with exit code two before installation.

    Parameters
    ----------
    harness : SimpleNamespace
        Command fixture recording plans, consent, results, and phase events.
    arguments : list of str
        CLI arguments selecting the behavior checked by this case.
    """
    result = CLI_RUNNER.invoke(cli.app, arguments)
    assert result.exit_code == 2
    assert "install" not in harness.events


def test_existing_destination_rejected(harness, tmp_path):
    """Preserve an existing destination and reject application creation.

    Parameters
    ----------
    harness : SimpleNamespace
        Command fixture recording plans, consent, results, and phase events.
    tmp_path : Path
        Temporary parent directory reserved for this test project.
    """
    (tmp_path / "blog").mkdir()
    result = CLI_RUNNER.invoke(cli.app, ["new", "blog", "--no-interaction"])
    assert result.exit_code == 2
    assert (tmp_path / "blog").is_dir()
    assert "install" not in harness.events


def test_non_tty_error_is_actionable_before_any_prerequisite(monkeypatch):
    """Require noninteractive options before checking tools without a TTY.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture replacing command or terminal dependencies for this case.
    """

    def no_tty():
        """Simulate streams without interactive terminal support.

        Returns
        -------
        bool
            False, indicating that interactive prompts are unavailable.
        """
        return False

    monkeypatch.setattr("orionis_installer.ui.has_tty", no_tty)
    result = CLI_RUNNER.invoke(cli.app, ["new"])
    assert result.exit_code == 2
    assert "--no-interaction" in result.output


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (PrerequisiteError("Git missing"), 2),
        (CompatibilityError("Extra missing"), 2),
        (ProcessError("Download failed"), 1),
        (KeyboardInterrupt(), 130),
        (EOFError(), 130),
    ],
)
def test_prerequisite_errors_and_cancellation(harness, monkeypatch, error, code):
    """Classify prerequisite failures and cancellation with stable exit codes.

    Parameters
    ----------
    harness : SimpleNamespace
        Command fixture recording plans, consent, results, and phase events.
    monkeypatch : pytest.MonkeyPatch
        Fixture replacing command or terminal dependencies for this case.
    error : BaseException
        Injected prerequisite failure or user cancellation.
    code : int
        Expected command exit status for the injected failure.
    """

    def fail(*args, **kwargs):
        """Raise the injected prerequisite failure or cancellation.

        Parameters
        ----------
        *args : tuple
            Positional arguments accepted by the substituted phase callback.
        **kwargs : dict
            Keyword arguments accepted by the substituted phase callback.

        Raises
        ------
        BaseException
            Always, with the failure injected by this parameterized case.
        """
        raise error

    monkeypatch.setattr(cli, "check_prerequisites", fail)
    result = CLI_RUNNER.invoke(cli.app, ["new", "--no-interaction"])
    assert result.exit_code == code
    assert "banner" not in harness.events


@pytest.mark.parametrize("phase", ["text", "select", "confirm", "install", "post"])
def test_cancellation_each_cli_phase(harness, monkeypatch, phase):
    """Stop execution when the user cancels any command phase.

    Parameters
    ----------
    harness : SimpleNamespace
        Command fixture recording plans, consent, results, and phase events.
    monkeypatch : pytest.MonkeyPatch
        Fixture replacing command or terminal dependencies for this case.
    phase : str
        Command phase replaced with an interrupting callback.
    """

    def cancel(*args, **kwargs):
        """Interrupt the selected phase without continuing to later operations.

        Parameters
        ----------
        *args : tuple
            Positional arguments accepted by the substituted phase callback.
        **kwargs : dict
            Keyword arguments accepted by the substituted phase callback.

        Raises
        ------
        KeyboardInterrupt
            Always, to simulate cancellation in the selected phase.
        """
        raise KeyboardInterrupt

    if phase == "install":
        monkeypatch.setattr(cli.Installer, "install", cancel)
    elif phase == "post":
        monkeypatch.setattr(cli, "run_post_install", cancel)
    else:
        monkeypatch.setattr(cli.UI, phase, cancel)
    result = CLI_RUNNER.invoke(cli.app, ["new"])
    assert result.exit_code == 130
    assert harness.events[-1] == ("warning", MESSAGES["cancelled"])


def test_declined_plan_does_not_clone(harness, monkeypatch):
    """Skip installation when the user declines the displayed plan.

    Parameters
    ----------
    harness : SimpleNamespace
        Command fixture recording plans, consent, results, and phase events.
    monkeypatch : pytest.MonkeyPatch
        Fixture replacing command or terminal dependencies for this case.
    """

    def decline(self, label, default):
        """Reject plan confirmation without opening a terminal.

        Parameters
        ----------
        label : str
            Question text recorded for prompt-order assertions.
        default : bool
            Proposed consent ignored by the rejecting callback.

        Returns
        -------
        bool
            False, declining application creation.
        """
        return False

    monkeypatch.setattr(cli.UI, "confirm", decline)
    result = CLI_RUNNER.invoke(cli.app, ["new", "blog"])
    assert result.exit_code == 0
    assert "install" not in harness.events


@pytest.mark.parametrize(
    "arguments",
    [["--no-color", "new", "--no-interaction"], ["new", "--no-color", "--no-interaction"]],
)
def test_global_and_command_color_flags(harness, arguments):
    """Accept color suppression before or after the command name.

    Parameters
    ----------
    harness : SimpleNamespace
        Command fixture recording plans, consent, results, and phase events.
    arguments : list of str
        CLI arguments selecting the behavior checked by this case.
    """
    result = CLI_RUNNER.invoke(cli.app, arguments)
    assert result.exit_code == 0, result.exception
    assert harness.ui_options["no_color"]


def test_partial_post_failure_returns_three(harness, monkeypatch):
    """Report requested post-install failure with exit code three.

    Parameters
    ----------
    harness : SimpleNamespace
        Command fixture recording plans, consent, results, and phase events.
    monkeypatch : pytest.MonkeyPatch
        Fixture replacing command or terminal dependencies for this case.
    """

    def partial(result, *args, **kwargs):
        """Simulate Git failure while preserving independent skipped operations.

        Parameters
        ----------
        result : InstallationResult
            Application result captured or updated by the simulated phase.
        *args : tuple
            Positional arguments accepted by the substituted phase callback.
        **kwargs : dict
            Keyword arguments accepted by the substituted phase callback.
        """
        result.git = State.FAILED
        result.migrations = result.editor = State.SKIPPED

    monkeypatch.setattr(cli, "run_post_install", partial)
    result = CLI_RUNNER.invoke(cli.app, ["new", "--no-interaction", "--git"])
    assert result.exit_code == 3
    assert harness.result.git == State.FAILED
