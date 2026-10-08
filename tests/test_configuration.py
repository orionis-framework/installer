"""Verify environment generation against minimal skeleton contracts."""

from pathlib import Path

import pytest

from orionis_installer.configuration import (
    configure_environment, literal_value, read_env, valid_sqlite_path,
)
from orionis_installer.models import Database, InstallationPlan


@pytest.mark.parametrize("database", [value for value in Database if value != Database.ALL])
def test_minimal_environment_supports_database(tmp_path: Path, database: Database) -> None:
    """Generate every concrete database from the skeleton's minimal environment.

    Parameters
    ----------
    tmp_path : Path
        Isolated application directory.
    database : Database
        Concrete connection selected from the installation menu.
    """
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
    if database == Database.SQLITE:
        assert values["DB_DATABASE"] == "database/database.sqlite"
    else:
        assert literal_value(values["DB_HOST"]) == "127.0.0.1"
        assert literal_value(values["DB_USERNAME"]) == "configure-me"
        assert literal_value(values["DB_DATABASE"]) == plan.name
        if database == Database.ORACLE:
            assert literal_value(values["DB_SERVICE_NAME"]) == "configure-me"


@pytest.mark.parametrize("value", [".", "./", "", ":memory:", "../data.sqlite", "file:test"])
def test_sqlite_rejects_nonpersistent_paths(value: str) -> None:
    """Reject paths that do not identify a persistent application-local file.

    Parameters
    ----------
    value : str
        Unsafe or non-file SQLite path.
    """
    assert not valid_sqlite_path(value)
