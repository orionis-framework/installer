"""Test terminal behavior with controlled streams and prompt-toolkit input."""

from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path

import pytest
from prompt_toolkit import Application
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.validation import ValidationError as PromptValidationError

from orionis_installer.exceptions import ValidationError
from orionis_installer.models import InstallationPlan, InstallationResult, Stack, State
from orionis_installer.ui import UI
from orionis_installer.ui.output import Output
from orionis_installer.ui.prompts import InputValidator, Prompts, selector_text
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
    assert plan.source.branch in rendered
    assert "Stack" in rendered and plan.source.label in rendered
    assert "master" not in rendered


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
    assert "uv run python -B reactor migrate" in rendered
    assert "--seed" not in rendered
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
    assert "reactor migrate" not in stream.getvalue()


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
    assert "uv run python -B reactor migrate" in lines


@pytest.mark.parametrize("stack", list(Stack))
def test_summary_renders_selected_stack_and_complete_repository(tmp_path, stack):
    """Show the exact chosen source without abbreviation in the review panel.

    Parameters
    ----------
    tmp_path : Path
        Temporary parent directory reserved for this test project.
    stack : Stack
        Catalog entry whose repository and branch must be visible.
    """
    stream = StringIO()
    plan = InstallationPlan("sample", tmp_path / "sample", stack=stack)
    Output(no_color=True, file=stream, width=104).summary(plan)
    rendered = stream.getvalue()
    assert plan.source.repository in rendered
    assert plan.source.branch in rendered
    assert plan.source.label in rendered
    assert "…" not in rendered
    assert "\x1b" not in rendered


def test_summary_folds_long_literal_metadata_without_markup(tmp_path):
    """Keep long metadata complete while displaying Rich-like text literally.

    Parameters
    ----------
    tmp_path : Path
        Temporary parent directory reserved for this test project.
    """
    stream = StringIO()
    description = "[bold]" + "long-description-" * 9 + "END[/bold]"
    plan = InstallationPlan("sample", tmp_path / "sample", description=description)
    Output(no_color=True, file=stream, width=36).summary(plan)
    rendered = stream.getvalue()
    compact = "".join(line.strip(" │|") for line in rendered.splitlines()).replace(" ", "")
    assert description in compact
    assert "…" not in rendered
    assert "\x1b" not in rendered


def test_selector_active_marker_default_and_literal_caption():
    """Keep selection and defaults distinct without interpreting caption markup."""
    choices = [("blank", "Blank"), ("ssr", "[bold]SSR[/bold]\x1b[2J")]
    fragments = selector_text(choices, 1, "blank")
    rendered = fragment_list_to_text(fragments)
    assert "> 02" in rendered
    assert "Blank  (default)" in rendered
    assert "[bold]SSR[/bold]" in rendered
    assert "\x1b" not in rendered
    assert any(style == "class:selected" and "SSR" in value for style, value in fragments)


@pytest.mark.parametrize(
    ("keys", "expected"),
    [("\x1b[C\r", "b"), ("\t\r", "b"), ("\x1b[Z\r", "c")],
)
def test_selector_additional_keyboard_navigation(keys, expected):
    """Support horizontal arrows, Tab, and Shift+Tab for keyboard navigation.

    Parameters
    ----------
    keys : str
        Keyboard bytes sent through a controlled input pipe.
    expected : str
        Machine value accepted after navigation.
    """
    with (
        create_pipe_input() as input_stream,
        create_app_session(input=input_stream, output=DummyOutput()),
    ):
        input_stream.send_text(keys)
        assert (
            Prompts(no_color=True).select(
                "Choose", [("a", "One"), ("b", "Two"), ("c", "Three")], "a"
            )
            == expected
        )


@pytest.mark.parametrize(("keys", "expected"), [("y\r", True), ("n\r", False)])
def test_confirmation_shortcuts_remain_reviewable_until_enter(keys, expected):
    """Select a direct Yes/No answer before Enter accepts the confirmation.

    Parameters
    ----------
    keys : str
        Confirmation shortcut followed by explicit acceptance.
    expected : bool
        Decision accepted by the confirmation application.
    """
    with (
        create_pipe_input() as input_stream,
        create_app_session(input=input_stream, output=DummyOutput()),
    ):
        input_stream.send_text(keys)
        assert Prompts(no_color=True).confirm("Continue?", not expected) is expected


def test_selector_rejects_ambiguous_duplicate_values():
    """Reject duplicate machine values before opening the selection application."""
    with pytest.raises(ValueError, match="unique machine values"):
        Prompts(no_color=True).select("Choose", [("a", "One"), ("a", "Two")], "a")


def test_non_terminal_progress_reports_real_stage_transitions():
    """Write stable, sanitized logs without animation or fabricated percentages."""
    stream = StringIO()
    output = Output(no_color=True, file=stream, width=80)
    with output.progress() as progress:
        progress.step("Clone [bold]source[/bold]\x1b[2J")
        assert progress.states == [State.RUNNING, State.PENDING, State.PENDING, State.PENDING]
        progress.step("Configure")
        assert progress.states[0] == State.COMPLETED
        progress.step("Synchronize")
        progress.step("Verify")
    assert progress.states == [State.COMPLETED] * 4
    rendered = stream.getvalue()
    assert "01/04" in rendered and "04/04" in rendered
    assert "[bold]source[/bold]" in rendered
    assert "completed and verified" in rendered
    assert "\x1b" not in rendered and "%" not in rendered


@pytest.mark.parametrize(
    ("exception", "state"), [(ValueError("failure"), State.FAILED), (EOFError(), State.CANCELLED)]
)
def test_progress_preserves_failure_or_cancellation(exception, state):
    """Mark only the active stage interrupted and propagate its original exception.

    Parameters
    ----------
    exception : BaseException
        Failure or cancellation raised inside the controlled progress context.
    state : State
        Expected final state of the interrupted active operation.
    """
    stream = StringIO()
    with pytest.raises(type(exception)), Output(no_color=True, file=stream).progress() as progress:
        progress.step("Clone")
        progress.step("Configure")
        raise exception
    assert progress.states == [State.COMPLETED, state, State.PENDING, State.PENDING]
    assert "Installation interrupted" in stream.getvalue()
    assert "All installation stages completed" not in stream.getvalue()


@pytest.mark.parametrize("width", [24, 36, 80])
def test_progress_timeline_fits_narrow_output(width):
    """Keep timeline states and diagnostics readable without truncation.

    Parameters
    ----------
    width : int
        Controlled console width used to check progress wrapping.
    """
    stream = StringIO()
    output = Output(no_color=True, file=stream, width=width)
    progress = output.progress()
    progress.current = 2
    progress.states = [State.COMPLETED, State.COMPLETED, State.RUNNING, State.PENDING]
    progress.detail = "Synchronizing application dependencies."
    progress.started = 1.0
    output.console.print(progress)
    rendered = stream.getvalue()
    assert "…" not in rendered and "\x1b" not in rendered
    assert all(len(line) <= width for line in rendered.splitlines())


def test_ascii_output_supports_banner_rules_and_status_panels(tmp_path):
    """Render every output surface safely when a legacy stream requires ASCII.

    Parameters
    ----------
    tmp_path : Path
        Temporary parent directory reserved for this test project.
    """
    buffer = BytesIO()
    stream = TextIOWrapper(buffer, encoding="ascii")
    output = Output(no_color=True, file=stream, width=80)
    plan = InstallationPlan("sample", tmp_path / "sample")
    output.banner()
    output.section("Application", "Choose a starting point.")
    output.summary(plan)
    output.error("Example failure.")
    output.final(InstallationResult(plan, creation=State.COMPLETED, published=True))
    stream.flush()
    rendered = buffer.getvalue().decode("ascii")
    assert "ORIONIS" in rendered
    assert "Example failure." in rendered
    assert "reactor serve" in rendered
    assert "\x1b" not in rendered


@pytest.mark.parametrize("width", [24, 80])
def test_selector_renders_active_guidance_in_narrow_terminal(monkeypatch, width):
    """Render the selected option and its complete safe guidance in an actual layout.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture attaching a screen observer to the prompt application.
    width : int
        Terminal width used by the prompt-toolkit output adapter.
    """
    screens = []
    real_run = Application.run

    class NarrowOutput(DummyOutput):
        """Expose a controlled terminal size for the real prompt renderer."""

        def get_size(self):
            """Return the controlled size without reading a physical terminal.

            Returns
            -------
            Size
                Terminal dimensions used by the prompt renderer.
            """
            return Size(rows=24, columns=width)

    def observe(application):
        """Capture the visible screen after prompt-toolkit completes a redraw.

        Parameters
        ----------
        application : Application
            Running selector whose virtual screen is ready for inspection.
        """
        screen = application.renderer.last_rendered_screen
        if screen is not None:
            lines = []
            for row in screen.data_buffer.values():
                line = "".join(cell.char for _, cell in sorted(row.items())).rstrip()
                if line:
                    lines.append(line)
            screens.append("\n".join(lines))

    def run(application):
        """Attach a screen observer and run the real keyboard selection.

        Parameters
        ----------
        application : Application
            Selector application constructed by the production prompt interface.

        Returns
        -------
        str
            Accepted machine value returned by the real application.
        """
        application.after_render += observe
        return real_run(application)

    monkeypatch.setattr(Application, "run", run)
    with (
        create_pipe_input() as input_stream,
        create_app_session(input=input_stream, output=NarrowOutput()),
    ):
        input_stream.send_text("\r")
        value = Prompts(no_color=True).select(
            "Choose a stack\x1b[2J",
            [("blank", "Blank"), ("ssr", "SSR")],
            "ssr",
            descriptions={"ssr": "Server rendered pages. [literal]\x1b]0;unsafe\x07"},
        )
    assert value == "ssr"
    visible = "".join(screens).replace("\n", "")
    assert "SSR" in visible and "> 02" in visible
    assert "Server rendered pages." in visible and "[literal]" in visible
    assert "unsafe" not in visible and "\x1b" not in visible
    assert all(len(line) <= width for screen in screens for line in screen.splitlines())
