"""Cross-platform validation protects destinations and gives precise errors."""

import os
import subprocess

import pytest

from orionis_installer.exceptions import ValidationError
from orionis_installer.validation import (
    is_redirect,
    validate_destination,
    validate_email,
    validate_name,
    validate_text,
)


@pytest.mark.parametrize("name", ["blog", "my-app", "app_2026", "my.app", "a", "123", "A" * 100])
def test_portable_names_are_accepted(name):
    """Verify that portable names are accepted.

    Parameters
    ----------
    name : str
        Application name whose acceptance or rejection is being checked.
    """
    assert validate_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../app",
        "app/child",
        "app\\child",
        "my app",
        "app;cmd",
        "a..b",
        "-app",
        "app-",
        "app.",
        "CON",
        "con.txt",
        "NUL",
        "COM1",
        "lpt9.txt",
        "CONIN$",
        "applicatiön",
        "博客",
        "app\n",
        "app\x1b[31m",
        "a" * 101,
    ],
)
def test_names_fail_without_silent_normalization(name):
    """Verify rejection of unsafe names without silently normalizing them.

    Parameters
    ----------
    name : str
        Application name whose acceptance or rejection is being checked.
    """
    with pytest.raises(ValidationError):
        validate_name(name)


@pytest.mark.parametrize("email", ["person@example.com", "a+b@example.co.uk", "ñ@example.com"])
def test_reasonable_email_formats(email):
    """Verify that reasonable email formats pass local validation.

    Parameters
    ----------
    email : str
        Author email whose local validation is being checked.
    """
    assert validate_email(email) == email


@pytest.mark.parametrize(
    "email",
    [
        "",
        "person",
        "a@localhost",
        "a@@example.com",
        "a b@example.com",
        "a@example..com",
        "a@.example.com",
        "a@example.com\n",
        "a" * 250 + "@example.com",
    ],
)
def test_invalid_email_is_local_validation(email):
    """Verify rejection of invalid email syntax without network access.

    Parameters
    ----------
    email : str
        Author email whose local validation is being checked.
    """
    with pytest.raises(ValidationError):
        validate_email(email)


@pytest.mark.parametrize("value", ["ñ 🐍", "quotes ' \" # $ ${value} \\", ""])
def test_unicode_and_printable_text_are_supported(value):
    """Verify acceptance of Unicode and printable text without alteration.

    Parameters
    ----------
    value : str
        Literal or control-containing text exercised by the parameterized case.
    """
    assert validate_text(value, "Text") == value


@pytest.mark.parametrize(
    "value", ["\x00", "line\nnext", "\t", "\x1b[31m", "\x7f", "\x80", "\x85", "\x9f"]
)
def test_terminal_control_characters_are_rejected(value):
    """Verify that terminal control characters are rejected.

    Parameters
    ----------
    value : str
        Literal or control-containing text exercised by the parameterized case.
    """
    with pytest.raises(ValidationError):
        validate_text(value, "Text")


def test_destination_with_spaces_is_returned_absolute_without_creation(tmp_path):
    """Verify absolute resolution of a spaced destination without creating it.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    target = tmp_path / "project with spaces & symbols"
    assert validate_destination(target) == target.absolute()
    assert not target.exists()


@pytest.mark.parametrize("kind", ["empty-directory", "directory-with-user-file", "file"])
def test_every_existing_destination_is_rejected_and_preserved(tmp_path, kind):
    """Verify that every existing destination is rejected and preserved.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    kind : str
        Existing filesystem entry type placed at the proposed destination.
    """
    target = tmp_path / "application"
    if kind == "file":
        target.write_text("user file", encoding="utf-8")
    else:
        target.mkdir()
        if kind != "empty-directory":
            (target / "user.txt").write_text("user data", encoding="utf-8")
    with pytest.raises(ValidationError, match="already exists"):
        validate_destination(target)
    assert target.exists()
    if kind == "directory-with-user-file":
        assert (target / "user.txt").read_text(encoding="utf-8") == "user data"


def test_missing_parent_and_traversal_are_rejected(tmp_path):
    """Verify rejection of missing destination parents and traversal components.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    with pytest.raises(ValidationError):
        validate_destination(tmp_path / "absent" / "application")
    with pytest.raises(ValidationError):
        validate_destination(tmp_path / ".." / "application")


def test_uv_workspace_ancestor_is_rejected_without_manifest_mutation(tmp_path):
    """Verify rejection of uv workspace ancestry without changing its manifest.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    manifest = tmp_path / "pyproject.toml"
    original = '[tool.uv.workspace]\nmembers = ["packages/*"]\n'
    manifest.write_text(original, encoding="utf-8")
    with pytest.raises(ValidationError, match="workspace"):
        validate_destination(tmp_path / "application")
    assert manifest.read_text(encoding="utf-8") == original


def test_ordinary_ancestral_project_is_allowed(tmp_path):
    """Verify acceptance of an ordinary project ancestor without a uv workspace.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    (tmp_path / "pyproject.toml").write_text('[project]\nname="parent"\n', encoding="utf-8")
    assert validate_destination(tmp_path / "application") == tmp_path / "application"


def test_unreadable_ancestral_manifest_is_rejected(tmp_path):
    """Verify rejection of an ancestor manifest with invalid TOML syntax.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    (tmp_path / "pyproject.toml").write_text("not valid [toml", encoding="utf-8")
    with pytest.raises(ValidationError):
        validate_destination(tmp_path / "application")


def test_destination_under_symlink_is_rejected(tmp_path):
    """Verify rejection of a destination below a symbolic link.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"OS does not permit symlink creation: {exc}")
    assert is_redirect(alias)
    with pytest.raises(ValidationError, match="link|junction"):
        validate_destination(alias / "application")


@pytest.mark.skipif(os.name != "nt", reason="Junctions are specific to Windows")
def test_destination_under_windows_junction_is_rejected(tmp_path):
    """Verify rejection of a destination below a Windows junction.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    real = tmp_path / "real target"
    real.mkdir()
    alias = tmp_path / "junction alias"
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(real)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        pytest.skip("Windows did not permit a disposable junction")
    try:
        assert is_redirect(alias)
        with pytest.raises(ValidationError, match="link|junction"):
            validate_destination(alias / "application")
    finally:
        # rmdir removes this junction itself, never the real target directory.
        alias.rmdir()
    assert real.is_dir()
