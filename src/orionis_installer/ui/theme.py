import re
from rich.theme import Theme

_BOLD_CYAN = "bold #4cc9f0"

THEME = Theme(
    {
        "orionis": _BOLD_CYAN,
        "logo_outline": "bold #134675",
        "logo_star": "bold #f4c430",
        "logo_spark": _BOLD_CYAN,
        "accent": "#4cc9f0",
        "success": "#76dbac",
        "warning": "#f4c430",
        "error": "bold #ff8d9c",
        "muted": "#a0aec0",
        "border": "#385575",
        "heading": "bold #eef6ff",
        "number": _BOLD_CYAN,
        "command": "#d7edff",
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
