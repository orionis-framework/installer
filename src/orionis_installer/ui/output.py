import os
import sys
import time
from collections import Counter
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

_LOGO_PIXELS = (
    "......................BBBBB.......................",
    "....................BBBBBBBBB.....................",
    "...................BBB.....BBB....................",
    ".................BBB.........BBB..................",
    "...............BBBB...........BBB.................",
    "..............BBB...............BBB...............",
    "............BBBB.................BBB..............",
    "......C....BBB.....................BBB............",
    "......C..BBBB...........Y...........BBB...........",
    ".....CC..BB.............Y.............BBB.........",
    ".....CCC...............YYY.............BBB........",
    "....CCCC...............YYY...............BBB......",
    "..CCCCCCCCC............YYY................BBB.....",
    "CCCCCCCCCCCCC..........YYY..................BB....",
    ".CCCCCCCCCC............YYY...................BB...",
    "....CCCC...............YYY...................BB...",
    ".....CCC...............YYYY..................BB...",
    "..BB.CCC..............YYYYY..................BB...",
    "..BB..C...............YYYYY..................BB...",
    "..BB..C...............YYYYY..................BB...",
    "..BB..C..............YYYYYYY.................BB...",
    "..BB................YYYYYYYYY................BB...",
    "..BB..........YYYYYYYYYYYYYYYYYYYY...........BB...",
    "..BB....YYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY....BB...",
    "..BB....YYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY....BB...",
    "..BB.......YYYYYYYYYYYYYYYYYYYYYYYYYYY.......BB...",
    "..BB..............YYYYYYYYYYYYY..............BB...",
    "..BB.................YYYYYYY.................BB...",
    "..BB..................YYYYY..................BB...",
    "..BB..................YYYYY...............C..BB...",
    "..BB..................YYYYY..............CC..BB...",
    "..BB..................YYYYY..............CCC.BB...",
    "..BB...................YYYY..............CCC.BB...",
    "..BB...................YYY...............CCC......",
    "..BB...................YYY.............CCCCCCC....",
    "..BBB..................YYY..........CCCCCCCCCCCCC.",
    "...BBB.................YYY............CCCCCCCCC...",
    ".....BBB...............YYY..............CCCCC.....",
    "......BBB..............YYY............B..CCC......",
    ".......BBBB.............Y............BBB.CCC......",
    ".........BBB............Y..........BBBB..CCC......",
    "..........BBBB....................BBB.....C.......",
    "............BBB..................BBB......C.......",
    "..............BBB..............BBB................",
    "...............BBB............BBB.................",
    ".................BBB........BBB...................",
    "..................BBBB.....BBB....................",
    "....................BBBBBBBBB.....................",
    ".....................BBBBBB.......................",
    "..................................................",
    "..................................................",
    "..................................................",
)


def _compact_logo_pixels() -> tuple[str, ...]:
    """Reduce the bitmap to two-by-two blocks and pad the final raster.

    Returns
    -------
    tuple of str
        Compact bitmap retaining the dominant color of each block.
    """
    pixels = _LOGO_PIXELS
    reduced: list[str] = []
    for row in range(0, len(pixels), 2):
        cells: list[str] = []
        for column in range(0, len(pixels[0]), 2):
            block_colors = Counter(
                pixels[row + dy][column + dx]
                for dy in range(2)
                for dx in range(2)
                if pixels[row + dy][column + dx] != "."
            )
            cells.append(block_colors.most_common(1)[0][0] if block_colors else ".")
        reduced.append("".join(cells) + ".")
    return (*reduced, "." * 26, "." * 26)


def _brand_logo(*, compact: bool = False) -> Text:
    """
    Render the official logo bitmap with eight subpixels per terminal cell.

    Parameters
    ----------
    compact : bool, optional
        Reduce the bitmap to seven terminal rows for the welcome screen.

    Returns
    -------
    Text
        Unicode raster retaining the outline and the three-color palette.
    """
    palette = {
        "B": "logo_outline",
        "Y": "logo_star",
        "C": "logo_spark",
    }
    dot_pixels = (
        (0, 0, 0),
        (0, 1, 3),
        (1, 0, 1),
        (1, 1, 4),
        (2, 0, 2),
        (2, 1, 5),
        (3, 0, 6),
        (3, 1, 7),
    )
    pixels = _compact_logo_pixels() if compact else _LOGO_PIXELS
    lines: list[Text] = []
    for row in range(0, len(pixels), 4):
        line = Text()
        for column in range(0, len(pixels[0]), 2):
            dots = 0
            colors: Counter[str] = Counter()
            for row_offset, column_offset, bit in dot_pixels:
                color = pixels[row + row_offset][column + column_offset]
                if color != ".":
                    dots |= 1 << bit
                    colors[color] += 1
            if colors:
                color = colors.most_common(1)[0][0]
                line.append(chr(0x2800 + dots), style=palette[color])
            else:
                line.append(" ")
        lines.append(line)
    return Text("\n").join(lines)


class Output:
    """Present plans, progress, diagnostics, and executable next steps."""

    def __init__(
        self, *, no_color: bool = False, file: TextIO | None = None, width: int | None = None
    ) -> None:
        """
        Configure the output console and honor monochrome settings.

        Parameters
        ----------
        no_color : bool, optional
            Disable color; ``NO_COLOR`` also disables it when present.
        file : TextIO or None, optional
            Destination stream; ``None`` uses standard output.
        width : int or None, optional
            Rendering width in characters; ``None`` uses console detection.

        Returns
        -------
        None
            Store the configured Rich console.
        """
        monochrome = no_color or "NO_COLOR" in os.environ
        stream = file if file is not None else sys.stdout
        self.console = Console(
            file=file,
            width=width,
            theme=THEME,
            no_color=monochrome,
            color_system=None if monochrome else "auto",
            force_terminal=None if stream.isatty() else False,
            markup=False,
            highlight=False,
        )

    @property
    def panel_width(self) -> int:
        """
        Return the panel width capped at 104 characters.

        Returns
        -------
        int
            Available console width, limited to 104 characters.
        """
        return min(self.console.width, 104)

    @property
    def line_character(self) -> str:
        """
        Choose a divider compatible with the output encoding.

        Returns
        -------
        str
            ASCII dash in ASCII-only mode, otherwise a Unicode horizontal line.
        """
        return "-" if self.console.options.ascii_only else "─"

    def message(self, message: str) -> None:
        """
        Display an informational message without interpreting terminal controls.

        Parameters
        ----------
        message : str
            Message to sanitize and render as literal text.

        Returns
        -------
        None
            Write the message to the console.
        """
        self.console.print(Text(terminal_text(message), style="accent"))

    def warning(self, message: str) -> None:
        """
        Display a sanitized warning with a visible prefix.

        Parameters
        ----------
        message : str
            Warning describing a limitation or pending configuration.

        Returns
        -------
        None
            Write the warning to the console, including in monochrome mode.
        """
        self.console.print(
            Text(MESSAGES["warning_prefix"] + terminal_text(message), style="warning")
        )

    def error(self, message: str) -> None:
        """
        Display a sanitized error in a responsive diagnostic panel.

        Parameters
        ----------
        message : str
            Failure description; omit credentials and other secrets.

        Returns
        -------
        None
            Write the error panel to the console.
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
        """
        Render the installer banner with a terminal-compatible layout.

        Returns
        -------
        None
            Display the brand, installer version, and required runtime without network access.
        """
        identity = Text("ORIONIS", style="heading")
        identity.append("\nAPPLICATION INSTALLER", style="accent")
        identity.append("\n" + MESSAGES["tagline"], style="muted")
        separator = " | " if self.console.options.ascii_only else " · "
        identity.append("\n\nInstaller ", style="muted")
        identity.append("v" + __version__, style="accent")
        identity.append("\nPython 3.14" + separator + "uv", style="muted")
        if self.panel_width >= 70 and not self.console.options.ascii_only:
            logo = _brand_logo(compact=True)
            layout = Table.grid(padding=(0, 3))
            layout.add_column(width=13)
            layout.add_column(ratio=1, vertical="middle")
            layout.add_row(logo, identity)
            content: Text | Table = layout
        else:
            content = identity
        self.console.print()
        self.console.print(
            Panel(
                content,
                border_style="logo_outline",
                box=box.ROUNDED,
                padding=(1, 2) if self.panel_width >= 54 else (0, 1),
                width=self.panel_width,
            )
        )

    def section(self, label: str, detail: str = "") -> None:
        """
        Introduce a wizard section with a divider and optional guidance.

        Parameters
        ----------
        label : str
            Section heading to sanitize and render as literal text.
        detail : str, optional
            Guidance displayed below the divider only when nonempty.

        Returns
        -------
        None
            Write the divider, optional guidance, and surrounding spacing.
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
        """
        Record an accepted selection in the console output.

        Parameters
        ----------
        label : str
            Question text to map to a short label when available.
        caption : str
            Selected answer to sanitize and retain in the output.

        Returns
        -------
        None
            Write the accepted answer with its label and success marker.
        """
        short_labels = {
            MESSAGES["stack"]: LABELS["stack"],
            MESSAGES["storage"]: LABELS["drivers_storage"],
            MESSAGES["default_storage"]: LABELS["storage"],
            MESSAGES["database"]: LABELS["drivers_database"],
            MESSAGES["default_database"]: LABELS["database"],
            MESSAGES["git"]: LABELS["git"],
            MESSAGES["migrate"]: LABELS["migrations"],
        }
        line = Text("  + ", style="success")
        line.append(terminal_text(short_labels.get(label, label)) + "  ", style="muted")
        line.append(terminal_text(caption), style="heading")
        self.console.print(line)

    def _table(self, rows: list[tuple[str, object]]) -> Table:
        """
        Build a wrapping label-value grid for the available panel width.

        Parameters
        ----------
        rows : list[tuple[str, object]]
            Pairs of ``LABELS`` keys and values in display order.

        Returns
        -------
        Table
            Single-column grid below 54 characters; two-column grid otherwise.
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
        """
        Print the complete destination without Rich wrapping or truncation.

        Parameters
        ----------
        path : Path
            Project directory to display as a labeled path.

        Returns
        -------
        None
            Write the labeled, sanitized destination.
        """
        self.console.print(
            Text(LABELS["path"] + ": " + terminal_text(path), style="muted"),
            no_wrap=True,
            overflow="ignore",
            crop=False,
        )

    def _command(self, command: str) -> None:
        """
        Print a complete shell command without Rich wrapping or truncation.

        Parameters
        ----------
        command : str
            Shell command with arguments already quoted for the target shell.

        Returns
        -------
        None
            Write the sanitized command in the command style.
        """
        self.console.print(
            Text(terminal_text(command), style="command"),
            no_wrap=True,
            overflow="ignore",
            crop=False,
        )

    def _group(self, title: str, rows: list[tuple[str, object]]) -> Group:
        """
        Group an uppercase heading with a responsive configuration grid.

        Parameters
        ----------
        title : str
            Trusted interface heading to convert to uppercase.
        rows : list[tuple[str, object]]
            Pairs of ``LABELS`` keys and values to display below the heading.

        Returns
        -------
        Group
            Heading, blank line, and label-value grid as one renderable.
        """
        return Group(Text(title.upper(), style="accent"), Text(""), self._table(rows))

    def summary(self, plan: InstallationPlan) -> None:
        """
        Display the installation plan and full destination path.

        Parameters
        ----------
        plan : InstallationPlan
            Project metadata, source selection, drivers, and dependency extras.

        Returns
        -------
        None
            Write the responsive summary panel and destination path.
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
            ("storage", dict(STORAGE_CHOICES)[plan.active_storage.value]),
            ("database", dict(DATABASE_CHOICES)[plan.active_database.value]),
            ("extras", ", ".join(plan.extras) or MESSAGES["no_extras"]),
        ]
        if plan.storage != plan.active_storage:
            services.insert(1, ("drivers_storage", dict(STORAGE_CHOICES)[plan.storage.value]))
        if plan.database != plan.active_database:
            services.insert(-1, ("drivers_database", dict(DATABASE_CHOICES)[plan.database.value]))
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

    def final(self, result: InstallationResult) -> None:  # NOSONAR
        """
        Display installation outcomes, warnings, and available next steps.

        Parameters
        ----------
        result : InstallationResult
            Project plan, observed outcomes, runtime versions, and warnings.

        Returns
        -------
        None
            Write results and warnings, adding commands only for published projects.
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
            self._nextSteps(result)

    def _nextSteps(self, result: InstallationResult) -> None:
        """
        Print ordered recovery, migration, and stack-specific launch commands.

        Parameters
        ----------
        result : InstallationResult
            Published project's verified creation and migration states.

        Returns
        -------
        None
            Display preparation before launch and explain separate worker processes.
        """
        from orionis_installer.post_install import migration_commands

        if result.creation != State.COMPLETED:
            self.message(MESSAGES["recovery_hint"])
            self._command("uv sync --python 3.14")
            self._command("uv run python -B reactor key:generate")
            return
        if result.migrations != State.COMPLETED:
            commands = migration_commands(result)
            if commands:
                self.console.print()
                self.warning(MESSAGES["migration_pending"])
                for command in commands:
                    self._command("uv run python -B reactor " + command)
                self.console.print()
        stack = result.plan.stack.value
        if stack == "slim_worker":
            self._command("uv run python -B reactor queue:work")
            self.console.print()
            self.message(MESSAGES["worker_hint"])
            self._command("uv run python -B reactor schedule:work")
        elif stack == "blank":
            self.message(MESSAGES["blank_hint"])
            self._command("uv run python -B reactor list")
        else:
            self._command("uv run python -B reactor serve")
        if stack == "slim_mcp":
            self.console.print()
            self.message(MESSAGES["mcp_hint"])
            self.console.print(Text(MESSAGES["mcp_inspector"], style="accent"))

    def progress(self) -> InstallationProgress:
        """
        Create a context manager for the four installation stages.

        Returns
        -------
        InstallationProgress
            Unstarted progress context using this output console.
        """
        return InstallationProgress(self)


class InstallationProgress:
    """Track actual stage transitions without estimating network completion."""

    def __init__(self, output: Output) -> None:
        """
        Initialize pending stages, timing state, and the progress spinner.

        Parameters
        ----------
        output : Output
            Console and rendering helpers shared with the installer.

        Returns
        -------
        None
            Store the output and initialize the four pending stages.
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
        self.stage_started = 0.0
        self.durations = [0.0] * len(self.labels)
        self.live: Live | None = None
        self.spinner = Spinner("dots", style="accent")

    def __enter__(self) -> InstallationProgress:
        """
        Start progress timing and select live or static rendering.

        Returns
        -------
        InstallationProgress
            This context with animation enabled only on capable terminals.
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
        """
        Advance the stage timeline and display the active operation.

        Parameters
        ----------
        message : str
            Operation detail to sanitize and display for the active stage.

        Returns
        -------
        None
            Complete the prior stage, start the next, and refresh or print progress.
        """
        if self.current >= 0:
            self.states[self.current] = State.COMPLETED
            self.durations[self.current] = time.monotonic() - self.stage_started
        self.current = min(self.current + 1, len(self.labels) - 1)
        self.states[self.current] = State.RUNNING
        self.stage_started = time.monotonic()
        self.detail = terminal_text(message)
        if self.live is not None:
            self.live.refresh()
        else:
            line = Text(f"  {self.current + 1:02d}/{len(self.labels):02d}  ", style="number")
            line.append(self.labels[self.current] + "  ", style="heading")
            line.append(self.detail, style="muted")
            self.output.console.print(line)

    def _row(self, table: Table, index: int, label: str, state: State, *, compact: bool) -> None:
        """Add the stage caption and optional status and timing cells.

        Parameters
        ----------
        table : Table
            Timeline grid configured for the selected terminal layout.
        index : int
            Stage position used for its number and recorded duration.
        label : str
            Stage description displayed in the timeline.
        state : State
            Current stage status.
        compact : bool
            Omit separate indicator and timing columns on narrow terminals.

        Returns
        -------
        None
            Append one row matching the timeline's selected layout.
        """
        marker, style = _STATES[state]
        caption = Text(f"{index + 1:02d} {label}", style=style, overflow="fold")
        if compact:
            caption.append("  " + state.value, style=style)
            table.add_row(caption)
            return
        indicator: Text | Spinner = (
            self.spinner
            if state == State.RUNNING and not self.output.console.options.ascii_only
            else Text(marker, style=style)
        )
        duration = (
            time.monotonic() - self.stage_started
            if state == State.RUNNING
            else self.durations[index]
        )
        timing = f"{max(0, duration):.1f}s" if state != State.PENDING else ""
        table.add_row(
            indicator, caption, Text(state.value, style=style), Text(timing, style="muted")
        )

    def __rich__(self) -> Panel:
        """
        Render stage states, the active operation, and elapsed time.

        Returns
        -------
        Panel
            Responsive timeline with elapsed time instead of a completion estimate.
        """
        table = Table.grid(padding=(0, 2), expand=True)
        compact = self.output.panel_width < 54
        table.add_column(ratio=1 if compact else None, width=None if compact else 2)
        if not compact:
            table.add_column(ratio=1)
            table.add_column()
            table.add_column(justify="right")
        for index, (label, state) in enumerate(zip(self.labels, self.states, strict=True)):
            self._row(table, index, label, state, compact=compact)
        elapsed = max(0, int(time.monotonic() - self.started))
        footer = Text(self.detail, style="muted", overflow="fold")
        completed = self.states.count(State.COMPLETED)
        track = Text("[", style="border")
        for state in self.states:
            marker, style = _STATES[state]
            track.append("=" if state == State.COMPLETED else marker, style=style)
        track.append(f"]  {completed}/{len(self.labels)} stages complete", style="muted")
        return Panel(
            Group(track, Text(""), table, Text(""), footer),
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
        """
        Finalize the active stage and stop progress rendering.

        Distinguish successful completion, cancellation, and failure.

        Parameters
        ----------
        exception_type : type[BaseException] or None
            Raised exception type, or ``None`` after a successful context body.
        exception : BaseException or None
            Raised exception instance used to distinguish cancellation from failure.
        traceback : TracebackType or None
            Context-body traceback, or ``None`` when no exception was raised.

        Returns
        -------
        None
            Leave any exception raised by the context body unsuppressed.
        """
        if self.current >= 0:
            self.durations[self.current] = time.monotonic() - self.stage_started
            if exception_type is None:
                current_state = State.COMPLETED
            elif isinstance(exception, (Cancelled, KeyboardInterrupt, EOFError)):
                current_state = State.CANCELLED
            else:
                current_state = State.FAILED
            self.states[self.current] = current_state
        if exception_type is None and self.current == len(self.labels) - 1:
            self.detail = MESSAGES["progress_completed"]
        elif exception_type is not None:
            self.detail = MESSAGES["progress_interrupted"]
        if self.live is not None:
            self.live.refresh()
            self.live.stop()
        elif self.current >= 0:
            self.output.console.print(self)


def state_text(state: State) -> Text:
    """
    Format an operation state with its marker and semantic style.

    Parameters
    ----------
    state : State
        Operation status to display.

    Returns
    -------
    Text
        Styled marker and state value, readable without color.
    """
    marker, style = _STATES[state]
    return Text(marker + " " + state.value, style=style)


def quote_directory(path: Path) -> str:
    """
    Quote a directory argument for the host platform's shell.

    Parameters
    ----------
    path : Path
        Destination for the displayed change-directory command.

    Returns
    -------
    str
        Sanitized argument using PowerShell quoting on Windows or POSIX quoting otherwise.
    """
    value = terminal_text(path)
    if os.name == "nt":
        return "'" + value.replace("'", "''") + "'"
    import shlex

    return shlex.quote(value)
