"""Test terminal behavior with controlled streams and prompt-toolkit input."""

from io import StringIO
from pathlib import Path

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.validation import ValidationError as PromptValidationError

from orionis_installer.exceptions import ValidationError
from orionis_installer.models import InstallationPlan, InstallationResult, State
from orionis_installer.ui import UI
from orionis_installer.ui.output import Output
from orionis_installer.ui.prompts import InputValidator, Prompts
from orionis_installer.ui.theme import terminal_text
from orionis_installer.validation import validate_name


@pytest.mark.parametrize("width", [24, 36, 80])
def test_narrow_ascii_banner_without_terminal_controls(width):
    """Fit the banner to narrow output without control sequences.

    Parameters
    ----------
    width : int
        Controlled console width used to check banner wrapping.
    """
    stream = StringIO()
    output = Output(no_color=True, file=stream, width=width)
    output.banner()
    rendered = stream.getvalue()
    assert "ORIONIS" in rendered
    assert "\x1b" not in rendered
    assert all(len(line) <= width for line in rendered.splitlines())


def test_no_color_environment(monkeypatch):
    """Honor an empty NO_COLOR variable in rendering and prompts.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture replacing command or terminal dependencies for this case.
    """
    monkeypatch.setenv("NO_COLOR", "")
    assert UI().no_color
    assert UI().console.no_color


def test_markup_and_control_sequences_are_never_interpreted():
    """Strip terminal commands while preserving literal Rich markup."""
    stream = StringIO()
    output = Output(no_color=True, file=stream, width=100)
    value = "[bold red]Ana[/bold red]\x1b[2J\x1b]0;malicious\x07\r\x00"
    output.message(value)
    assert stream.getvalue() == "[bold red]Ana[/bold red]\n"
    assert terminal_text("A\x85B\x7fC") == "ABC"


def test_summary_shows_effective_driver_and_required_extra(tmp_path):
    """Display active drivers alongside deterministic aggregate extras.

    Parameters
    ----------
    tmp_path : Path
        Temporary parent directory reserved for this test project.
    """
    stream = StringIO()
    from orionis_installer.models import Database, Storage

    plan = InstallationPlan(
        "sample", tmp_path / "sample", storage=Storage.ALL, database=Database.ALL
    )
    Output(no_color=True, file=stream, width=130).summary(plan)
    rendered = stream.getvalue()
    assert "database,factories,storage" in rendered
    assert "Default disk" in rendered and "Local" in rendered
    assert "Default connection" in rendered and "SQLite" in rendered
    assert "skeleton@master" in rendered


def test_final_reports_real_states_versions_and_recovery(tmp_path):
    """Report verified versions and failed or skipped post-install states.

    Parameters
    ----------
    tmp_path : Path
        Temporary parent directory reserved for this test project.
    """
    stream = StringIO()
    plan = InstallationPlan("sample", tmp_path / "project with spaces")
    result = InstallationResult(
        plan,
        creation=State.COMPLETED,
        git=State.SKIPPED,
        migrations=State.FAILED,
        editor=State.SKIPPED,
        python_version="3.14.6",
        framework_version="1.2.3",
        warnings=["Review migrations."],
        published=True,
    )
    Output(no_color=True, file=stream, width=130).final(result)
    rendered = stream.getvalue()
    assert "3.14.6" in rendered and "1.2.3" in rendered
    assert "failed" in rendered and "skipped" in rendered
    assert "Application ready." not in rendered
    assert "uv run python -B reactor serve" in rendered
    assert "uv run python -B reactor migrate --seed" in rendered
    assert str(plan.path) in rendered


def test_final_completed_migrations_omit_migration_command(tmp_path):
    """Omit migration recovery instructions after verified completion.

    Parameters
    ----------
    tmp_path : Path
        Temporary parent directory reserved for this test project.
    """
    stream = StringIO()
    result = InstallationResult(
        InstallationPlan("sample", tmp_path / "sample"),
        creation=State.COMPLETED,
        migrations=State.COMPLETED,
        published=True,
    )
    Output(no_color=True, file=stream, width=130).final(result)
    assert "migrate --seed" not in stream.getvalue()


def test_ui_rejects_non_tty_before_prompt(monkeypatch):
    """Reject every prompt interface before invoking its input implementation.

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
    ui = UI(file=StringIO())

    def read_text():
        """Exercise the guarded text-input interface.

        Raises
        ------
        ValidationError
            If text input correctly rejects the detached terminal.
        """
        return ui.text("Name")

    def choose():
        """Exercise the guarded choice-selection interface.

        Raises
        ------
        ValidationError
            If choice input correctly rejects the detached terminal.
        """
        return ui.select("Select", [("a", "A")], "a")

    def confirm():
        """Exercise the guarded consent interface.

        Raises
        ------
        ValidationError
            If consent input correctly rejects the detached terminal.
        """
        return ui.confirm("Continue", True)

    for action in (read_text, choose, confirm):
        with pytest.raises(ValidationError, match="--no-interaction"):
            action()


@pytest.mark.parametrize(
    ("keys", "default", "expected"),
    [("\r", "a", "a"), ("\x1b[B\r", "a", "b"), ("\x1b[A\r", "a", "c")],
)
def test_selector_arrows_enter_and_defaults(keys, default, expected):
    """Navigate with arrow keys and accept the selected machine value.

    Parameters
    ----------
    keys : str
        Keyboard bytes sent through the controlled input pipe.
    default : str
        Machine value initially selected before keyboard navigation.
    expected : str
        Machine value expected after the navigation sequence.
    """
    with (
        create_pipe_input() as input_stream,
        create_app_session(input=input_stream, output=DummyOutput()),
    ):
        input_stream.send_text(keys)
        assert (
            Prompts(no_color=True).select(
                "Select", [("a", "One"), ("b", "Two"), ("c", "Three")], default
            )
            == expected
        )


@pytest.mark.parametrize(("keys", "exception"), [("\x03", KeyboardInterrupt), ("\x04", EOFError)])
def test_selector_cancellation_restores_application(keys, exception):
    """Propagate cancellation or EOF from a controlled selector session.

    Parameters
    ----------
    keys : str
        Keyboard bytes sent through the controlled input pipe.
    exception : type of BaseException
        Cancellation or EOF expected from the selector.
    """
    with (
        create_pipe_input() as input_stream,
        create_app_session(input=input_stream, output=DummyOutput()),
    ):
        input_stream.send_text(keys)
        with pytest.raises(exception):
            Prompts(no_color=True).select("Select", [("a", "One")], "a")


def test_text_default_and_validator():
    """Accept the editable default and reject invalid input immediately."""
    with (
        create_pipe_input() as input_stream,
        create_app_session(input=input_stream, output=DummyOutput()),
    ):
        input_stream.send_text("\r")
        assert Prompts(no_color=True).text("Name", "sample", validate_name) == "sample"
    validator = InputValidator(validate_name)
    with pytest.raises(PromptValidationError, match="Invalid name"):
        validator.validate(Document("../invalid"))


def test_validator_accepts_unicode_without_rewriting():
    """Preserve Unicode author text through prompt validation."""
    from orionis_installer.validation import validate_text

    def validate_author(value):
        """Validate printable author text without changing its characters.

        Parameters
        ----------
        value : str
            Unicode author text passed to the pure validator.

        Returns
        -------
        str
            Original author text after printable-character validation.

        Raises
        ------
        ValidationError
            If the input contains terminal control characters.
        """
        return validate_text(value, "Author")

    validator = InputValidator(validate_author)
    validator.validate(Document("Zoë Müller"))


def test_default_directory_command_handles_quote():
    """Quote apostrophes and spaces in displayed directory commands."""
    from orionis_installer.ui.output import quote_directory

    quoted = quote_directory(Path("team's project"))
    assert quoted.startswith("'")
    assert "team" in quoted and "project" in quoted


def test_long_destination_and_commands_remain_complete_in_narrow_output(tmp_path):
    """Keep full paths and commands copyable in narrow output.

    Parameters
    ----------
    tmp_path : Path
        Temporary parent directory reserved for this test project.
    """
    from orionis_installer.ui.output import quote_directory

    path = tmp_path / ("long-name-" * 8 + " space")
    plan = InstallationPlan("sample", path)
    stream = StringIO()
    output = Output(no_color=True, file=stream, width=30)
    output.summary(plan)
    output.final(InstallationResult(plan, creation=State.COMPLETED, published=True))
    lines = stream.getvalue().splitlines()
    assert "Destination: " + str(path) in lines
    assert "cd " + quote_directory(path) in lines
    assert "uv run python -B reactor serve" in lines
    assert "uv run python -B reactor migrate --seed" in lines
