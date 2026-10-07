"""Cross-platform names and destinations, checked without mutating them."""

import os
import re
import stat
from pathlib import Path

import tomlkit

from orionis_installer.exceptions import ValidationError
from orionis_installer.messages import MESSAGES

RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)
}


def validate_text(value: str, label: str) -> str:
    """Reject terminal control characters without changing printable text.

    Parameters
    ----------
    value : str
        User-supplied text to check.
    label : str
        Field label used in the validation diagnostic.

    Returns
    -------
    str
        Original text when no C0 or C1 control characters are present.

    Raises
    ------
    ValidationError
        If the text contains terminal control characters.
    """
    if any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value):
        raise ValidationError(MESSAGES["text_control_invalid"].format(label=label))
    return value


def validate_name(value: str) -> str:
    """Validate a portable application name without silently normalizing it.

    Parameters
    ----------
    value : str
        Proposed application and destination directory name.

    Returns
    -------
    str
        Original name when its syntax and length are portable.

    Raises
    ------
    ValidationError
        If the name contains unsafe syntax or a reserved Windows device name.
    """
    if (
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", value)
        or value.split(".")[0].upper() in RESERVED
        or ".." in value
        or len(value) > 100
    ):
        raise ValidationError(MESSAGES["name_invalid"])
    return value


def validate_email(value: str) -> str:
    """Validate a reasonable author email without network access.

    Parameters
    ----------
    value : str
        Optional author email supplied by the user.

    Returns
    -------
    str
        Original email when its syntax and length are acceptable.

    Raises
    ------
    ValidationError
        If the email contains controls or lacks a reasonable address format.
    """
    validate_text(value, MESSAGES["email_label"])
    if len(value) > 254 or not re.fullmatch(r"[^\s@<>]+@[^\s@<>.]+(?:\.[^\s@<>.]+)+", value):
        raise ValidationError(MESSAGES["email_invalid"])
    return value


def is_redirect(path: Path) -> bool:
    """Detect symbolic links and Windows reparse points without following them.

    Parameters
    ----------
    path : Path
        Existing filesystem entry to inspect.

    Returns
    -------
    bool
        Whether the entry can redirect filesystem operations elsewhere.
    """
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def validate_destination(path: Path) -> Path:
    """Verify an unused destination outside redirected paths and uv workspaces.

    Parameters
    ----------
    path : Path
        Proposed application directory whose parent must already exist.

    Returns
    -------
    Path
        Absolute destination path without creating or modifying any entry.

    Raises
    ------
    ValidationError
        If the destination exists, traverses redirects, or lies in a uv workspace.
    """
    path = path.absolute()
    if any(part in (".", "..") for part in path.parts):
        raise ValidationError(MESSAGES["destination_ambiguous"])
    if os.path.lexists(path):
        raise ValidationError(MESSAGES["destination_exists"])
    if not path.parent.is_dir():
        raise ValidationError(MESSAGES["destination_parent_missing"])
    for ancestor in (path.parent, *path.parent.parents):
        if is_redirect(ancestor):
            raise ValidationError(MESSAGES["destination_redirect"])
        manifest = ancestor / "pyproject.toml"
        if manifest.is_file():
            try:
                data = tomlkit.parse(manifest.read_text(encoding="utf-8"))
            except (ValueError, tomlkit.exceptions.ParseError) as exc:
                raise ValidationError(MESSAGES["ancestor_manifest_invalid"]) from exc
            if "workspace" in data.get("tool", {}).get("uv", {}):
                raise ValidationError(MESSAGES["ancestor_workspace_unsupported"])
    return path
