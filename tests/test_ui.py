"""Exercise terminal compatibility, deliberate keyboard input, and useful launch guidance."""

import io
from pathlib import Path
from unittest.mock import patch
import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from orionis_installer.models import InstallationPlan, InstallationResult, Stack, State
from orionis_installer.ui import UI
from orionis_installer.ui.messages import STACK_CHOICES
from orionis_installer.ui.prompts import Prompts

class EncodedStream(io.StringIO):
    """Simulate a redirected terminal with a specific output encoding."""

    def __init__(self, encoding: str) -> None:
        """Store the simulated stream encoding.

        Parameters
        ----------
        encoding : str
            Encoding reported to Rich during output negotiation.
        """
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self) -> str:
        """Return the simulated terminal encoding.

        Returns
        -------
        str
            Encoding chosen for this output stream.
        """
        return self._encoding

@pytest.mark.parametrize("width", [40, 60, 80, 120])
@pytest.mark.parametrize("encoding", ["ascii", "utf-8"])
def test_redirected_wizard_respects_width_and_encoding(
    width: int, encoding: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep progress and branding readable even when color is externally forced.

    Parameters
    ----------
    width : int
        Available number of terminal columns.
    encoding : str
        Encoding supported by the redirected stream.
    monkeypatch : pytest.MonkeyPatch
        Environment control for testing contradictory color settings.
    """
    monkeypatch.setenv("FORCE_COLOR", "1")
    stream = EncodedStream(encoding)
    ui = UI(file=stream, width=width)
    ui.banner()
    with ui.progress() as progress:
        for detail in ("Download source", "Write environment", "Install dependencies", "Verify"):
            progress.step(detail)
    output = stream.getvalue()
    assert "\x1b" not in output
    output.encode(encoding)
    assert all(len(line) <= width for line in output.splitlines())
    assert "4/4 stages complete" in output

def test_output_sanitizes_controls_without_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop injected terminal operations and honor the monochrome environment.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Environment control for the standard NO_COLOR preference.
    """
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    ui = UI(file=stream)
    ui.section("Title\x1b[2J", "Visible\x1b]52;c;hidden-secret\x07 guidance")
    ui.warning("Problem\r\n\x1b[31m remains")
    output = stream.getvalue()
    assert "\x1b" not in output
    assert "hidden-secret" not in output
    assert "Visible guidance" in output
    assert "Warning: Problem remains" in output

def test_number_shortcuts_reach_every_stack() -> None:
    """Select each catalog entry by number and explicitly confirm with Enter."""
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        prompts = Prompts(no_color=True)
        for number, (value, _) in enumerate(STACK_CHOICES, start=1):
            pipe.send_text(str(number) + "\r")
            assert prompts.select("Stack", STACK_CHOICES, "blank") == value

@pytest.mark.parametrize("cancel_key, failure", [("\x03", KeyboardInterrupt), ("\x04", EOFError)])
def test_number_shortcut_can_be_cancelled(cancel_key: str, failure: type[BaseException]) -> None:
    """Require Enter before committing a highlighted choice and propagate cancellation.

    Parameters
    ----------
    cancel_key : str
        Ctrl+C or Ctrl+D terminal input.
    failure : type[BaseException]
        Expected cancellation signal returned by the prompt application.
    """
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        pipe.send_text("7" + cancel_key)
        with pytest.raises(failure):
            Prompts(no_color=True).select("Stack", STACK_CHOICES, "blank")

def test_failed_progress_retains_completed_and_unstarted_stages() -> None:
    """Record actual durations without reporting unexecuted stages as complete."""
    stream = io.StringIO()
    with patch("orionis_installer.ui.output.time.monotonic", return_value=10.0) as clock:
        progress = UI(file=stream, width=80).progress()
        with pytest.raises(RuntimeError, match="failed"), progress:
            progress.step("Download")
            clock.return_value = 12.0
            progress.step("Configure")
            clock.return_value = 15.0
            raise RuntimeError("failed")
    assert progress.states == [State.COMPLETED, State.FAILED, State.PENDING, State.PENDING]
    assert progress.durations[:2] == [2.0, 3.0]
    assert "1/4 stages complete" in stream.getvalue()
    assert "All installation stages completed" not in stream.getvalue()

@pytest.mark.parametrize("width", [40, 80])
@pytest.mark.parametrize("encoding", ["ascii", "utf-8"])
@pytest.mark.parametrize("state", list(State))
def test_progress_rendering_preserves_stage_details(
    width: int, encoding: str, state: State
) -> None:
    """Render stage status and timing consistently in compact and wide layouts.

    Parameters
    ----------
    width : int
        Terminal width selecting the compact or multi-column layout.
    encoding : str
        Output encoding selecting ASCII indicators or Unicode spinners.
    state : State
        Status of the second stage in the rendered timeline.
    """
    stream = EncodedStream(encoding)
    progress = UI(file=stream, width=width, no_color=True).progress()
    progress.started = 10.0
    progress.stage_started = 12.0
    progress.states = [State.COMPLETED, state, State.PENDING, State.SKIPPED]
    progress.durations = [1.0, 2.5, 0.0, 0.0]
    progress.detail = "Verify selected dependencies"

    with patch("orionis_installer.ui.output.time.monotonic", return_value=15.0):
        progress.output.console.print(progress)

    output = stream.getvalue()
    output.encode(encoding)
    assert all(len(line) <= width for line in output.splitlines())
    assert state.value in output
    assert "00:05 elapsed" in output
    if width >= 54:
        assert "1.0s" in output
        if state == State.RUNNING:
            assert "3.0s" in output
        elif state != State.PENDING:
            assert "2.5s" in output
    else:
        assert "1.0s" not in output

def test_worker_guidance_prepares_queue_before_consuming(tmp_path: Path) -> None:
    """Prepare the selected queue backend before suggesting separate worker processes.

    Parameters
    ----------
    tmp_path : Path
        Published application directory with its selected queue backend.
    """
    (tmp_path / ".env").write_text(
        "QUEUE_CONNECTION=database\nDB_PASSWORD=never-print-this\n", encoding="utf-8"
    )
    result = InstallationResult(
        InstallationPlan("worker", tmp_path, stack=Stack.SLIM_WORKER),
        creation=State.COMPLETED,
        migrations=State.SKIPPED,
        published=True,
    )
    stream = io.StringIO()
    UI(file=stream, width=80).final(result)
    output = stream.getvalue()
    assert output.index("reactor queue:migrate") < output.index("reactor queue:work")
    assert "reactor schedule:work" in output
    assert "reactor serve" not in output
    assert "never-print-this" not in output
    assert "Visual Studio Code" not in output

@pytest.mark.parametrize("stack", [Stack.SLIM_REALTIME, Stack.SLIM_MCP])
def test_service_guidance_matches_the_installed_stack(tmp_path: Path, stack: Stack) -> None:
    """Offer the appropriate web entry point without an unnecessary database warning.

    Parameters
    ----------
    tmp_path : Path
        Published application's directory.
    stack : Stack
        Service stack used to determine its launch instructions.
    """
    result = InstallationResult(
        InstallationPlan("service", tmp_path, stack=stack),
        creation=State.COMPLETED,
        migrations=State.COMPLETED,
        published=True,
    )
    if stack == Stack.SLIM_REALTIME:
        result.migrations = State.SKIPPED
    stream = io.StringIO()
    UI(file=stream, width=80).final(result)
    output = stream.getvalue()
    assert "reactor serve" in output
    assert "Migrations pending" not in output
    if stack == Stack.SLIM_MCP:
        assert "https://inspector.orionis-framework.com/" in output

def test_incomplete_installation_offers_recovery_only(tmp_path: Path) -> None:
    """Avoid suggesting startup commands before dependencies have been verified.

    Parameters
    ----------
    tmp_path : Path
        Published project whose dependency installation failed.
    """
    stream = io.StringIO()
    result = InstallationResult(
        InstallationPlan("service", tmp_path, stack=Stack.MASTER),
        creation=State.FAILED,
        published=True,
    )
    UI(file=stream).final(result)
    assert "uv sync --python 3.14" in stream.getvalue()
    assert "uv run python -B reactor key:generate" in stream.getvalue()
    assert "reactor serve" not in stream.getvalue()
