"""Render a responsive, safe terminal interface and honest installation progress."""

import os
import time
from pathlib import Path
from types import TracebackType
from typing import TextIO

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from orionis_installer import __version__
from orionis_installer.exceptions import Cancelled
from orionis_installer.models import InstallationPlan, InstallationResult, State
from orionis_installer.ui.messages import DATABASE_CHOICES, LABELS, MESSAGES, STORAGE_CHOICES
from orionis_installer.ui.theme import THEME, terminal_text

_STATES = {
    State.PENDING: (".", "muted"),
    State.RUNNING: (">", "accent"),
    State.COMPLETED: ("+", "success"),
    State.SKIPPED: ("-", "muted"),
    State.FAILED: ("!", "error"),
    State.CANCELLED: ("!", "warning"),
}


class Output:
    """Present plans, progress, diagnostics, and executable next steps."""

    def __init__(
        self, *, no_color: bool = False, file: TextIO | None = None, width: int | None = None
    ) -> None:
        """Configure a shared console that honors monochrome environments.

        Parameters
        ----------
        no_color : bool, optional
            Whether to disable color in addition to honoring NO_COLOR.
        file : TextIO or None, optional
            Output stream, defaulting to standard output.
        width : int or None, optional
            Explicit rendering width, or the console-detected width.
        """
        self.console = Console(
            file=file,
            width=width,
            theme=THEME,
            no_color=no_color or "NO_COLOR" in os.environ,
            markup=False,
            highlight=False,
        )

    @property
    def panel_width(self) -> int:
        """Limit the reading width while fitting narrow terminals.

        Returns
        -------
        int
            Console width capped at 104 characters for comfortable reading.
        """
        return min(self.console.width, 104)

    @property
    def line_character(self) -> str:
        """Choose a divider that the output encoding can represent.

        Returns
        -------
        str
            ASCII dash for legacy output, or a Unicode line for modern terminals.
        """
        return "-" if self.console.options.ascii_only else "─"

    def message(self, message: str) -> None:
        """Display a sanitized informational message.

        Parameters
        ----------
        message : str
            Text to render without interpreting markup or control sequences.
        """
        self.console.print(Text(terminal_text(message), style="accent"))

    def warning(self, message: str) -> None:
        """Display a warning whose meaning remains clear without color.

        Parameters
        ----------
        message : str
            Diagnostic describing a limitation or pending configuration.
        """
        self.console.print(
            Text(MESSAGES["warning_prefix"] + terminal_text(message), style="warning")
        )

    def error(self, message: str) -> None:
        """Present an actionable failure in a distinct diagnostic panel.

        Parameters
        ----------
        message : str
            Failure diagnostic that excludes operational secrets.
        """
        self.console.print()
        self.console.print(
            Panel(
                Text(MESSAGES["error_prefix"] + terminal_text(message), style="error"),
                title=Text(MESSAGES["error_title"], style="error"),
                border_style="error",
                box=box.ROUNDED,
                padding=(1, 2) if self.panel_width >= 54 else (0, 1),
                width=self.panel_width,
            )
        )

    def banner(self) -> None:
        """Render a constellation identity with an adaptive compact layout."""
        identity = Text("ORIONIS", style="heading")
        identity.append("  /  INSTALLER", style="muted")
        identity.append("\n" + MESSAGES["tagline"], style="orionis")
        separator = " | " if self.console.options.ascii_only else " · "
        identity.append("\n\nPython 3.14" + separator + "uv" + separator, style="muted")
        identity.append("v" + __version__, style="accent")
        if self.panel_width >= 70 and not self.console.options.ascii_only:
            constellation = Text(
                "       ·       ✦\n   ·   ╲     ╱\n        ◆───·\n   ✧───╱     ╲\n               ·",
                style="orionis",
            )
            layout = Table.grid(padding=(0, 3))
            layout.add_column(width=19)
            layout.add_column(ratio=1)
            layout.add_row(constellation, identity)
            content: Text | Table = layout
        else:
            content = identity
        self.console.print()
        self.console.print(
            Panel(
                content,
                border_style="border",
                box=box.ROUNDED,
                padding=(1, 2) if self.panel_width >= 54 else (0, 1),
                width=self.panel_width,
            )
        )

    def section(self, label: str, detail: str = "") -> None:
        """Introduce a wizard section with a quiet divider and optional guidance.

        Parameters
        ----------
        label : str
            Section heading, rendered as literal terminal text.
        detail : str, optional
            Short contextual guidance displayed below the divider.
        """
        self.console.print()
        self.console.print(
            Rule(
                Text(terminal_text(label), style="orionis"),
                style="border",
                align="left",
                characters=self.line_character,
            )
        )
        if detail:
            self.console.print(Text(terminal_text(detail), style="muted"))
        self.console.print()

    def choice(self, label: str, caption: str) -> None:
        """Keep an accepted selection visible after its interactive menu closes.

        Parameters
        ----------
        label : str
            Question whose completed answer is recorded.
        caption : str
            Selected visible caption without terminal control sequences.
        """
        short_labels = {
            MESSAGES["stack"]: LABELS["stack"],
            MESSAGES["storage"]: LABELS["drivers_storage"],
            MESSAGES["default_storage"]: LABELS["storage"],
            MESSAGES["database"]: LABELS["drivers_database"],
            MESSAGES["default_database"]: LABELS["database"],
            MESSAGES["git"]: LABELS["git"],
            MESSAGES["migrate"]: LABELS["migrations"],
            MESSAGES["open"]: LABELS["editor"],
        }
        line = Text("  + ", style="success")
        line.append(terminal_text(short_labels.get(label, label)) + "  ", style="muted")
        line.append(terminal_text(caption), style="heading")
        self.console.print(line)

    def _table(self, rows: list[tuple[str, object]]) -> Table:
        """Build readable label-value rows that collapse on narrow terminals.

        Parameters
        ----------
        rows : list of tuple
            Label keys and values in presentation order.

        Returns
        -------
        Table
            Responsive grid using literal text and wrapping complete values.
        """
        table = Table.grid(padding=(0, 2), expand=True)
        compact = self.panel_width < 54
        table.add_column(style="muted", ratio=1 if compact else None)
        if not compact:
            table.add_column(ratio=1)
        for key, value in rows:
            rendered = value.copy() if isinstance(value, Text) else Text(terminal_text(value))
            rendered.overflow = "fold"
            rendered.no_wrap = False
            if compact:
                line = Text(LABELS[key] + ": ", style="muted", overflow="fold", no_wrap=False)
                line.append_text(rendered)
                table.add_row(line)
            else:
                table.add_row(Text(LABELS[key], style="muted", overflow="fold"), rendered)
        return table

    def _path(self, path: Path) -> None:
        """Keep a complete destination on one copyable output line.

        Parameters
        ----------
        path : Path
            Absolute project destination to display without truncation.
        """
        self.console.print(
            Text(LABELS["path"] + ": " + terminal_text(path), style="muted"),
            no_wrap=True,
            overflow="ignore",
            crop=False,
        )

    def _command(self, command: str) -> None:
        """Keep a shell command complete and easy to copy.

        Parameters
        ----------
        command : str
            Controlled command already quoted for the target shell.
        """
        self.console.print(
            Text(terminal_text(command), style="command"),
            no_wrap=True,
            overflow="ignore",
            crop=False,
        )

    def _group(self, title: str, rows: list[tuple[str, object]]) -> Group:
        """Compose a quiet heading and its configuration grid.

        Parameters
        ----------
        title : str
            Trusted interface heading.
        rows : list of tuple
            Label-value rows to display below the heading.

        Returns
        -------
        Group
            Heading, spacing, and responsive grid rendered as one block.
        """
        return Group(Text(title.upper(), style="accent"), Text(""), self._table(rows))

    def summary(self, plan: InstallationPlan) -> None:
        """Show the selected source and effective services before installation.

        Parameters
        ----------
        plan : InstallationPlan
            Validated application metadata, selected stack, and active drivers.
        """
        application: list[tuple[str, object]] = [
            ("name", Text(terminal_text(plan.name), style="heading")),
            ("description", plan.description),
        ]
        if plan.author_name or plan.author_email:
            application.append(
                (
                    "author",
                    " / ".join(value for value in (plan.author_name, plan.author_email) if value),
                )
            )
        source: list[tuple[str, object]] = [
            ("stack", plan.source.label),
            ("branch", plan.source.branch),
            ("python", "3.14.x"),
        ]
        services: list[tuple[str, object]] = [
            ("drivers_storage", dict(STORAGE_CHOICES)[plan.storage.value]),
            ("storage", dict(STORAGE_CHOICES)[plan.active_storage.value]),
            ("drivers_database", dict(DATABASE_CHOICES)[plan.database.value]),
            ("database", dict(DATABASE_CHOICES)[plan.active_database.value]),
            ("extras", ",".join(plan.extras)),
        ]
        if self.panel_width >= 92:
            columns = Table.grid(expand=True, padding=(0, 4))
            columns.add_column(ratio=1)
            columns.add_column(ratio=1)
            columns.add_row(
                self._group(MESSAGES["group_source"], source),
                self._group(MESSAGES["group_services"], services),
            )
            configuration: Table | Group = columns
        else:
            configuration = Group(
                self._group(MESSAGES["group_source"], source),
                Text(""),
                self._group(MESSAGES["group_services"], services),
            )
        self.console.print(
            Panel(
                Group(
                    self._table(application),
                    Text(""),
                    Rule(style="border", characters=self.line_character),
                    Text(""),
                    configuration,
                    Text(""),
                    self._table([("repository", plan.source.repository)]),
                ),
                title=Text(MESSAGES["summary"], style="orionis"),
                subtitle=Text(MESSAGES["summary_hint"], style="muted"),
                title_align="left",
                subtitle_align="right",
                border_style="border",
                box=box.ROUNDED,
                padding=(1, 2) if self.panel_width >= 54 else (0, 1),
                width=self.panel_width,
            )
        )
        self._path(plan.path)

    def final(self, result: InstallationResult) -> None:
        """Present verified outcomes and appropriate commands for the created project.

        Parameters
        ----------
        result : InstallationResult
            Observed creation, publication, and follow-up operation outcomes.
        """
        if result.creation == State.COMPLETED:
            ready = "ready_warnings" if result.warnings or result.exit_code else "ready"
            headline_style = "warning" if ready == "ready_warnings" else "success"
        else:
            ready, headline_style = "not_ready", "error"
        facts: list[tuple[str, object]] = [
            ("stack", result.plan.source.label + " / " + result.plan.source.branch),
            ("python", result.python_version or State.PENDING.value),
            ("framework", result.framework_version or State.PENDING.value),
            ("storage", dict(STORAGE_CHOICES)[result.plan.active_storage.value]),
            ("database", dict(DATABASE_CHOICES)[result.plan.active_database.value]),
        ]
        operations: list[tuple[str, object]] = [
            ("creation", state_text(result.creation)),
            (
                "factories",
                state_text(
                    State.COMPLETED if result.creation == State.COMPLETED else State.PENDING
                ),
            ),
            ("git", state_text(result.git)),
            ("migrations", state_text(result.migrations)),
            ("editor", state_text(result.editor)),
        ]
        if self.panel_width >= 92:
            columns = Table.grid(expand=True, padding=(0, 4))
            columns.add_column(ratio=1)
            columns.add_column(ratio=1)
            columns.add_row(
                self._group(MESSAGES["group_runtime"], facts),
                self._group(MESSAGES["group_operations"], operations),
            )
            outcomes: Table | Group = columns
        else:
            outcomes = Group(
                self._table(facts),
                Text(""),
                Rule(style="border", characters=self.line_character),
                Text(""),
                self._table(operations),
            )
        self.console.print()
        self.console.print(
            Panel(
                Group(
                    Text(MESSAGES[ready], style=headline_style),
                    Text(""),
                    outcomes,
                ),
                title=Text(MESSAGES["final"], style=headline_style),
                title_align="left",
                border_style=headline_style,
                box=box.ROUNDED,
                padding=(1, 2) if self.panel_width >= 54 else (0, 1),
                width=self.panel_width,
            )
        )
        self._path(result.plan.path)
        for warning in result.warnings:
            self.warning(warning)
        if result.published:
            self.section(MESSAGES["next"], MESSAGES["next_hint"])
            self._command("cd " + quote_directory(result.plan.path))
            self._command("uv run python -B reactor serve")
            if result.migrations != State.COMPLETED:
                self.console.print()
                self.warning(MESSAGES["migration_pending"])
                self._command("uv run python -B reactor migrate")

    def progress(self) -> InstallationProgress:
        """Create a context that tracks the installer's four ordered stages.

        Returns
        -------
        InstallationProgress
            Progress context whose step method accepts installer phase messages.
        """
        return InstallationProgress(self)


class InstallationProgress:
    """Track actual stage transitions without estimating network completion."""

    def __init__(self, output: Output) -> None:
        """Configure a stage timeline for the supplied output stream.

        Parameters
        ----------
        output : Output
            Shared console and output helpers used by the installer.
        """
        self.output = output
        self.labels = [
            MESSAGES["stage_source"],
            MESSAGES["stage_configuration"],
            MESSAGES["stage_dependencies"],
            MESSAGES["stage_verification"],
        ]
        self.states = [State.PENDING] * len(self.labels)
        self.current = -1
        self.detail = ""
        self.started = 0.0
        self.live: Live | None = None
        self.spinner = Spinner("dots", style="accent")

    def __enter__(self) -> InstallationProgress:
        """Start live rendering only when the output supports terminal animation.

        Returns
        -------
        InstallationProgress
            This context with an active timer and optional live display.
        """
        self.started = time.monotonic()
        self.output.console.print()
        if self.output.console.is_terminal and not self.output.console.is_dumb_terminal:
            self.live = Live(
                self,
                console=self.output.console,
                refresh_per_second=6,
                vertical_overflow="visible",
            )
            self.live.start()
        else:
            self.output.console.print(Text(MESSAGES["progress_title"], style="orionis"))
        return self

    def step(self, message: str) -> None:
        """Advance only when the installer reports a new operation.

        Parameters
        ----------
        message : str
            Sanitized description of the operation that is about to begin.
        """
        if self.current >= 0:
            self.states[self.current] = State.COMPLETED
        self.current = min(self.current + 1, len(self.labels) - 1)
        self.states[self.current] = State.RUNNING
        self.detail = terminal_text(message)
        if self.live is not None:
            self.live.refresh()
        else:
            line = Text(f"  {self.current + 1:02d}/{len(self.labels):02d}  ", style="number")
            line.append(self.labels[self.current] + "  ", style="heading")
            line.append(self.detail, style="muted")
            self.output.console.print(line)

    def __rich__(self) -> Panel:
        """Render current states, a real elapsed timer, and the active operation.

        Returns
        -------
        Panel
            Responsive timeline without a fabricated percentage estimate.
        """
        table = Table.grid(padding=(0, 2), expand=True)
        compact = self.output.panel_width < 54
        table.add_column(ratio=1 if compact else None, width=None if compact else 2)
        if not compact:
            table.add_column(ratio=1)
            table.add_column()
        for index, (label, state) in enumerate(zip(self.labels, self.states, strict=True)):
            marker, style = _STATES[state]
            indicator: Text | Spinner = (
                self.spinner
                if state == State.RUNNING and not self.output.console.options.ascii_only
                else Text(marker, style=style)
            )
            caption = Text(f"{index + 1:02d} {label}", style=style, overflow="fold")
            if compact:
                caption.append("  " + state.value, style=style)
                table.add_row(caption)
            else:
                table.add_row(indicator, caption, state_text(state))
        elapsed = max(0, int(time.monotonic() - self.started))
        footer = Text(self.detail, style="muted", overflow="fold")
        return Panel(
            Group(table, Text(""), footer),
            title=Text(MESSAGES["progress_title"], style="orionis"),
            subtitle=Text(f"{elapsed // 60:02d}:{elapsed % 60:02d} elapsed", style="muted"),
            title_align="left",
            border_style="border",
            box=box.ROUNDED,
            padding=(1, 2) if self.output.panel_width >= 54 else (0, 1),
            width=self.output.panel_width,
        )

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop animation and preserve the observed successful or interrupted state.

        Parameters
        ----------
        exception_type : type of BaseException or None
            Raised exception type, or None after a successful context body.
        exception : BaseException or None
            Exception instance used to distinguish cancellation from failure.
        traceback : TracebackType or None
            Original exception traceback, propagated unchanged by the context.
        """
        if self.current >= 0:
            self.states[self.current] = (
                State.COMPLETED
                if exception_type is None
                else State.CANCELLED
                if isinstance(exception, (Cancelled, KeyboardInterrupt, EOFError))
                else State.FAILED
            )
        if exception_type is None and self.current == len(self.labels) - 1:
            self.detail = MESSAGES["progress_completed"]
        elif exception_type is not None:
            self.detail = MESSAGES["progress_interrupted"]
        if self.live is not None:
            self.live.refresh()
            self.live.stop()
        elif self.current >= 0:
            self.output.console.print(
                Text("  " + self.detail, style=_STATES[self.states[self.current]][1])
            )


def state_text(state: State) -> Text:
    """Render a state with a visible marker and a matching color.

    Parameters
    ----------
    state : State
        Actual operation state to display.

    Returns
    -------
    Text
        Literal state caption that remains meaningful without color.
    """
    marker, style = _STATES[state]
    return Text(marker + " " + state.value, style=style)


def quote_directory(path: Path) -> str:
    """Quote a destination for PowerShell or a POSIX shell.

    Parameters
    ----------
    path : Path
        Project directory to use in the displayed change-directory command.

    Returns
    -------
    str
        Sanitized directory argument with shell-appropriate quoting.
    """
    value = terminal_text(path)
    if os.name == "nt":
        return "'" + value.replace("'", "''") + "'"
    import shlex

    return shlex.quote(value)
