"""Define the Orionis palette and sanitize terminal-bound text."""

import re

from rich.theme import Theme

THEME = Theme(
    {
        "orionis": "bold magenta",
        "accent": "cyan",
        "success": "green",
        "warning": "yellow",
        "error": "bold red",
        "muted": "dim",
    }
)

_ANSI = re.compile(r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-_])")


def terminal_text(value: object) -> str:
    """Remove terminal control sequences from an external value.

    Parameters
    ----------
    value : object
        Value to convert to text before removing ANSI, OSC, and control characters.

    Returns
    -------
    str
        Printable text that cannot issue terminal control commands.
    """
    text = _ANSI.sub("", str(value))
    return "".join(
        character for character in text if ord(character) >= 32 and not 127 <= ord(character) < 160
    )
