import re
from rich.theme import Theme

THEME = Theme(
    {
        "orionis": "bold #b1a2ff",
        "accent": "#78dce8",
        "success": "#8ce3b0",
        "warning": "#f5ca83",
        "error": "bold #ff8d9c",
        "muted": "#929bb0",
        "border": "#545f7a",
        "heading": "bold #eff1fa",
        "number": "bold #78dce8",
        "command": "#d9e1f2",
    }
)

_ANSI = re.compile(r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-_])")

def terminal_text(value: object) -> str:
    """
    Remove terminal control sequences from an external value.

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
