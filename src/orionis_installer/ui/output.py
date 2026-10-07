"""Render Rich output without interpreting untrusted markup or terminal controls."""

import os
from pathlib import Path
from typing import TextIO

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from orionis_installer.models import InstallationPlan, InstallationResult, State
from orionis_installer.ui.messages import DATABASE_CHOICES, LABELS, MESSAGES, STORAGE_CHOICES
from orionis_installer.ui.theme import THEME, terminal_text


class Output:
    """Render truthful installation plans, diagnostics, and next steps."""

    def __init__(
        self, *, no_color: bool = False, file: TextIO | None = None, width: int | None = None
    ) -> None:
        """Configure the Rich console for a stream and terminal width.

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

    def message(self, message: str) -> None:
        """Display a sanitized phase or informational message.

        Parameters
        ----------
        message : str
            Text to render with the Orionis accent style.
        """
        self.console.print(Text(terminal_text(message), style="accent"))

    def warning(self, message: str) -> None:
        """Display a sanitized warning with its visible prefix.

        Parameters
        ----------
        message : str
            Diagnostic describing a limitation or pending configuration.
        """
        self.console.print(
            Text(MESSAGES["warning_prefix"] + terminal_text(message), style="warning")
        )

    def error(self, message: str) -> None:
        """Display a sanitized failure diagnostic with its visible prefix.

        Parameters
        ----------
        message : str
            Actionable failure message that excludes operational secrets.
        """
        self.console.print(Text(MESSAGES["error_prefix"] + terminal_text(message), style="error"))

    def banner(self) -> None:
        """Render the ASCII Orionis identity within the available console width."""
        banner = Text("* .  ORIONIS  . *", style="orionis")
        if self.console.width >= 58:
            banner.append("\n    .  *-----*  .", style="accent")
        banner.append("\n" + MESSAGES["tagline"], style="accent")
        self.console.print(Panel(banner, border_style="orionis", box=box.ASCII, padding=(0, 1)))

    def _table(self, rows: list[tuple[str, object]]) -> Table:
        """Build a table with translated labels and printable external values.

        Parameters
        ----------
        rows : list of tuple
            Label keys and values to display in their given order.

        Returns
        -------
        Table
            Two-column grid that folds long values without interpreting markup.
        """
        table = Table.grid(padding=(0, 2))
        table.add_column(style="accent", no_wrap=False)
        table.add_column()
        for key, value in rows:
            table.add_row(Text(LABELS[key]), Text(terminal_text(value), overflow="fold"))
        return table

    def _path(self, path: Path) -> None:
        """Display a complete destination without renderer truncation.

        Parameters
        ----------
        path : Path
            Absolute project destination to keep on one copyable output line.
        """
        self.console.print(
            Text(LABELS["path"] + ": " + terminal_text(path), style="accent"),
            no_wrap=True,
            overflow="ignore",
            crop=False,
        )

    def _command(self, command: str) -> None:
        """Display a complete command without inserting line breaks.

        Parameters
        ----------
        command : str
            Controlled command text already quoted for the target shell.
        """
        self.console.print(Text(command), no_wrap=True, overflow="ignore", crop=False)

    def summary(self, plan: InstallationPlan) -> None:
        """Present the validated choices before downloading the skeleton.

        Parameters
        ----------
        plan : InstallationPlan
            Application metadata, destination, active drivers, and required extras.
        """
        rows: list[tuple[str, object]] = [
            ("name", plan.name),
            ("description", plan.description),
        ]
        if plan.author_name or plan.author_email:
            rows.append(
                (
                    "author",
                    " / ".join(value for value in (plan.author_name, plan.author_email) if value),
                )
            )
        rows += [
            ("python", "3.14.x"),
            ("drivers_storage", dict(STORAGE_CHOICES)[plan.storage.value]),
            ("storage", dict(STORAGE_CHOICES)[plan.active_storage.value]),
            ("drivers_database", dict(DATABASE_CHOICES)[plan.database.value]),
            ("database", dict(DATABASE_CHOICES)[plan.active_database.value]),
            ("extras", ",".join(plan.extras)),
            ("source", "skeleton@master"),
        ]
        self.console.print(
            Panel(
                self._table(rows), title=MESSAGES["summary"], border_style="accent", box=box.ASCII
            )
        )
        self._path(plan.path)

    def final(self, result: InstallationResult) -> None:
        """Report verified versions, operation states, warnings, and next commands.

        Parameters
        ----------
        result : InstallationResult
            Actual creation and post-install outcomes, including publication state.
        """
        if result.creation == State.COMPLETED:
            ready = "ready_warnings" if result.warnings or result.exit_code else "ready"
            self.console.print(
                Text(MESSAGES[ready], style="warning" if ready == "ready_warnings" else "success")
            )
        else:
            self.warning(MESSAGES["not_ready"])
        rows: list[tuple[str, object]] = [
            ("creation", result.creation.value),
            ("python", result.python_version or State.PENDING.value),
            ("framework", result.framework_version or State.PENDING.value),
            ("storage", dict(STORAGE_CHOICES)[result.plan.active_storage.value]),
            ("database", dict(DATABASE_CHOICES)[result.plan.active_database.value]),
            (
                "factories",
                State.COMPLETED.value
                if result.creation == State.COMPLETED
                else State.PENDING.value,
            ),
            ("git", result.git.value),
            ("migrations", result.migrations.value),
            ("editor", result.editor.value),
        ]
        self.console.print(
            Panel(self._table(rows), title=MESSAGES["final"], border_style="accent", box=box.ASCII)
        )
        self._path(result.plan.path)
        for warning in result.warnings:
            self.warning(warning)
        if result.published:
            self.console.print(Text(MESSAGES["next"], style="orionis"))
            self._command("cd " + quote_directory(result.plan.path))
            self._command("uv run python -B reactor serve")
            if result.migrations != State.COMPLETED:
                self.warning(MESSAGES["migration_pending"])
                self._command("uv run python -B reactor migrate --seed")


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
