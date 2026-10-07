"""Combine safe terminal output with guarded interactive input."""

import os
import sys
from collections.abc import Callable
from typing import TextIO

from orionis_installer.exceptions import ValidationError
from orionis_installer.ui.messages import MESSAGES
from orionis_installer.ui.output import Output
from orionis_installer.ui.prompts import Prompts


def has_tty() -> bool:
    """Check whether both input and output are attached to terminals.

    Returns
    -------
    bool
        Whether interactive prompts can use the current input and output streams.
    """
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


class UI(Output):
    """Coordinate rendering and interactive prompts for installer phases."""

    def __init__(
        self, *, no_color: bool = False, file: TextIO | None = None, width: int | None = None
    ) -> None:
        """Initialize output and prompts with matching color preferences.

        Parameters
        ----------
        no_color : bool, optional
            Whether to disable colors in both Rich and prompt-toolkit.
        file : TextIO or None, optional
            Rich output stream, defaulting to the terminal output stream.
        width : int or None, optional
            Optional render width used by controlled output tests.
        """
        self.no_color = no_color or "NO_COLOR" in os.environ
        super().__init__(no_color=self.no_color, file=file, width=width)
        self.prompts = Prompts(no_color=self.no_color)

    def require_tty(self) -> None:
        """Reject interactive input when either stream lacks a terminal.

        Raises
        ------
        ValidationError
            If prompting requires the caller to use noninteractive options.
        """
        if not has_tty():
            raise ValidationError(MESSAGES["no_tty"])

    def text(
        self,
        label: str,
        default: str = "",
        validator: Callable[[str], object] | None = None,
        password: bool = False,
    ) -> str:
        """Read a validated value from an interactive terminal.

        Parameters
        ----------
        label : str
            Question displayed before the input field.
        default : str, optional
            Editable initial value.
        validator : Callable or None, optional
            Callback that rejects invalid input without mutating project files.
        password : bool, optional
            Whether to hide the entered characters.

        Returns
        -------
        str
            Accepted input value.

        Raises
        ------
        ValidationError
            If the current streams do not support interaction.
        KeyboardInterrupt
            If the user cancels the prompt.
        EOFError
            If input closes before a value is accepted.
        """
        self.require_tty()
        return self.prompts.text(label, default, validator, password)

    def select(self, label: str, choices: list[tuple[str, str]], default: str) -> str:
        """Select and report a choice from an interactive terminal.

        Parameters
        ----------
        label : str
            Selection question.
        choices : list of tuple of str
            Ordered machine values and visible captions.
        default : str
            Machine value initially selected.

        Returns
        -------
        str
            Accepted machine value.

        Raises
        ------
        ValidationError
            If the current streams do not support interaction.
        ValueError
            If choices are empty or do not contain the default.
        KeyboardInterrupt
            If the user cancels the selection.
        EOFError
            If the user closes input before accepting a choice.
        """
        self.require_tty()
        value = self.prompts.select(label, choices, default)
        self.message(terminal_choice(label, choices, value))
        return value

    def confirm(self, label: str, default: bool) -> bool:
        """Request a yes-or-no decision from an interactive terminal.

        Parameters
        ----------
        label : str
            Consent question.
        default : bool
            Decision selected before keyboard navigation.

        Returns
        -------
        bool
            Whether the user accepted the proposed operation.

        Raises
        ------
        ValidationError
            If the current streams do not support interaction.
        KeyboardInterrupt
            If the user cancels the prompt.
        EOFError
            If the user closes input before accepting a decision.
        """
        self.require_tty()
        return self.prompts.confirm(label, default)


def terminal_choice(label: str, choices: list[tuple[str, str]], value: str) -> str:
    """Combine a selection question with its accepted caption.

    Parameters
    ----------
    label : str
        Selection question to repeat after accepting a choice.
    choices : list of tuple of str
        Machine values and their visible captions.
    value : str
        Accepted machine value.

    Returns
    -------
    str
        Question followed by the caption corresponding to the selected value.

    Raises
    ------
    KeyError
        If the selected value does not appear among the supplied choices.
    """
    return label + " " + dict(choices)[value]
