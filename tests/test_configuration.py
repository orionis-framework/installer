"""Verify environment generation against minimal skeleton contracts."""

import errno
import os
import time
from pathlib import Path

import pytest

from orionis_installer.configuration import (
    configure_environment, literal_value, read_env, set_literal_env, valid_sqlite_path,
)
from orionis_installer.models import Database, InstallationPlan


@pytest.mark.parametrize("database", [value for value in Database if value != Database.ALL])
@pytest.mark.parametrize("winerror", [None, 5, 32, 33])
def test_minimal_environment_supports_database(
    tmp_path: Path, database: Database, monkeypatch: pytest.MonkeyPatch, winerror: int | None,
) -> None:
    """Generate every concrete database from the skeleton's minimal environment.

    Parameters
    ----------
    tmp_path : Path
        Isolated application directory.
    database : Database
        Concrete connection selected from the installation menu.
    monkeypatch : pytest.MonkeyPatch
        Scoped replacement and retry-delay controls.
    winerror : int or None
        Windows error raised on each first replacement, or no simulated lock.
    """
    replace = os.replace
    attempts = 0
    delays: list[float] = []

    def replace_with_lock(source: Path, destination: Path) -> None:
        """Block each first replacement without changing the destination."""
        nonlocal attempts
        attempts += 1
        if winerror is not None and attempts % 2:
            error = PermissionError(errno.EACCES, "Environment temporarily locked")
            error.winerror = winerror
            raise error
        replace(source, destination)

    monkeypatch.setattr(os, "replace", replace_with_lock)
    monkeypatch.setattr(time, "sleep", delays.append)
    (tmp_path / "config").mkdir()
    (tmp_path / "database").mkdir()
    (tmp_path / ".env.example").write_text(
        'APP_NAME="Orionis"\nAPP_KEY=\nFILESYSTEM_DISK=local\n'
        'DB_CONNECTION=sqlite\nDB_DATABASE=database/database.sqlite\n',
        encoding="utf-8",
    )
    (tmp_path / "config" / "database.py").write_text(
        'default = Env.get("DB_CONNECTION")\n'
        'service = Env.get("DB_SERVICE_NAME")\n'
        'connections = Connections(sqlite=None, mysql=None, pgsql=None, '
        'oracle=None, sqlserver=None, redshift=None)\n',
        encoding="utf-8",
    )
    (tmp_path / "config" / "filesystems.py").write_text(
        'default = Env.get("FILESYSTEM_DISK")\ndisks = Disks(local=None)\n',
        encoding="utf-8",
    )
    plan = InstallationPlan(name="example-app", path=tmp_path, database=database)

    configure_environment(tmp_path, plan)

    values = read_env(tmp_path / ".env")
    assert values["DB_CONNECTION"] == database.value
    assert literal_value(values["APP_NAME"]) == plan.name
    assert "DB_PASSWORD" not in values
    assert not list(tmp_path.glob(".tmp_*"))
    assert len(delays) == (attempts // 2 if winerror is not None else 0)
    if database == Database.SQLITE:
        assert values["DB_DATABASE"] == "database/database.sqlite"
    else:
        assert literal_value(values["DB_HOST"]) == "127.0.0.1"
        assert literal_value(values["DB_USERNAME"]) == "configure-me"
        assert literal_value(values["DB_DATABASE"]) == plan.name
        if database == Database.ORACLE:
            assert literal_value(values["DB_SERVICE_NAME"]) == "configure-me"


@pytest.mark.parametrize(
    ("error_number", "winerror", "expected_attempts"),
    [
        (errno.EACCES, 5, 5),
        (errno.EACCES, 32, 5),
        (errno.EACCES, 33, 5),
        (errno.EACCES, None, 1),
        (errno.ENOSPC, None, 1),
    ],
)
def test_environment_write_preserves_file_on_persistent_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_number: int,
    winerror: int | None, expected_attempts: int,
) -> None:
    """Bound Windows retries and propagate other failures without changing the file.

    Parameters
    ----------
    tmp_path : Path
        Isolated directory containing the original environment file.
    monkeypatch : pytest.MonkeyPatch
        Scoped replacement and retry-delay controls.
    error_number : int
        Operating-system error raised on every replacement.
    winerror : int or None
        Optional native Windows error code.
    expected_attempts : int
        Maximum number of replacement attempts for this failure.
    """
    path = tmp_path / ".env"
    original = "# Preserve this comment\nAPP_NAME=original\nAPP_KEY=existing\n"
    path.write_text(original, encoding="utf-8")
    error = OSError(error_number, "Environment write denied")
    if winerror is not None:
        error.winerror = winerror
    attempts = 0
    delays: list[float] = []

    def deny_replacement(source: Path, destination: Path) -> None:
        """Keep the original file intact while reporting the same failure."""
        nonlocal attempts
        attempts += 1
        raise error

    monkeypatch.setattr(os, "replace", deny_replacement)
    monkeypatch.setattr(time, "sleep", delays.append)

    with pytest.raises(OSError) as caught:
        set_literal_env(path, "APP_NAME", "updated")

    assert caught.value is error
    assert attempts == expected_attempts
    assert len(delays) == expected_attempts - 1
    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".tmp_*"))


@pytest.mark.parametrize("value", [".", "./", "", ":memory:", "../data.sqlite", "file:test"])
def test_sqlite_rejects_nonpersistent_paths(value: str) -> None:
    """Reject paths that do not identify a persistent application-local file.

    Parameters
    ----------
    value : str
        Unsafe or non-file SQLite path.
    """
    assert not valid_sqlite_path(value)
