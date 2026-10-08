import os
import sys
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, TextIO
from orionis_installer.exceptions import ValidationError
from orionis_installer.ui.messages import MESSAGES
from orionis_installer.ui.output import Output
if TYPE_CHECKING:
    from orionis_installer.ui.prompts import Prompts

def has_tty() -> bool:
    """
    Check whether both input and output are attached to terminals.

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
        """
        Initialize output and prompts with matching color preferences.

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
        self._prompts: Prompts | None = None

    @property
    def prompts(self) -> Prompts:
        """Create the interactive prompt provider on first use.

        Returns
        -------
        Prompts
            Prompt provider shared by this interface's interactive operations.
        """
        if self._prompts is None:
            from orionis_installer.ui.prompts import Prompts

            self._prompts = Prompts(no_color=self.no_color)
        return self._prompts

    def requireTty(self) -> None:
        """
        Reject interactive input when either stream lacks a terminal.

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
        """
        Read a validated value from an interactive terminal.

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
        self.requireTty()
        return self.prompts.text(label, default, validator, password)

    def select(
        self,
        label: str,
        choices: list[tuple[str, str]],
        default: str,
        *,
        descriptions: Mapping[str, str] | None = None,
    ) -> str:
        """Select and report a choice from an interactive terminal.

        Parameters
        ----------
        label : str
            Selection question.
        choices : list of tuple of str
            Ordered machine values and visible captions.
        default : str
            Machine value initially selected.
        descriptions : Mapping of str to str or None, optional
            Contextual explanation displayed for the currently active option.

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
        self.requireTty()
        value = self.prompts.select(label, choices, default, descriptions=descriptions)
        self.choice(label, dict(choices)[value])
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
        self.requireTty()
        value = self.prompts.confirm(label, default)
        self.choice(label, MESSAGES["yes" if value else "no"])
        return value

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
