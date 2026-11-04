"""Verify environment generation against minimal skeleton contracts."""

import errno
import os
import time
from pathlib import Path
import pytest

from orionis_installer.configuration import (
    _inline_comment,
    configure_environment,
    configure_pyproject,
    literal_value,
    read_env,
    set_env_value,
    set_literal_env,
    valid_sqlite_path,
)
from orionis_installer.exceptions import CompatibilityError
from orionis_installer.models import Database, InstallationPlan, Stack, Storage

@pytest.mark.parametrize("stack", list(Stack))
@pytest.mark.parametrize("storage", list(Storage))
@pytest.mark.parametrize("database", list(Database))
def test_manifest_driver_extras(
    tmp_path: Path,
    stack: str,
    storage: Storage,
    database: Database,
) -> None:
    """Request only the framework extras required by the selected drivers.

    Parameters
    ----------
    tmp_path : Path
        Isolated application directory.
    stack : str
        Public stack identifier.
    storage : Storage
        Requested storage SDK selection.
    database : Database
        Requested database driver selection.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="template"\nrequires-python=">=3.14"\ndependencies=["orionis>=0.932.0"]\n',
        encoding="utf-8",
    )
    plan = InstallationPlan(
        name="example-app",
        path=tmp_path,
        stack=Stack(stack),
        storage=storage,
        database=database,
    )
    expected: set[str] = set()
    if storage != Storage.LOCAL:
        expected.add("storage" if storage == Storage.ALL else storage.value)
    if database != Database.SQLITE:
        expected.add("database" if database == Database.ALL else database.value)

    requirement = configure_pyproject(tmp_path, plan)

    assert plan.extras == tuple(sorted(expected))
    assert requirement.extras == expected
    assert str(requirement) == (
        "orionis" + (f"[{','.join(sorted(expected))}]" if expected else "") + ">=0.932.0"
    )

@pytest.mark.parametrize("database_directory", [False, True])
@pytest.mark.parametrize("database", [value for value in Database if value != Database.ALL])
@pytest.mark.parametrize("winerror", [None, 5, 32, 33])
def test_minimal_environment_supports_database(
    tmp_path: Path,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    winerror: int | None,
    database_directory: bool,
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
        Windows error raised on the first two replacement attempts,
        or no simulated lock.
    database_directory : bool
        Whether the template already includes a SQLite file and its parent directory.
    """
    replace = os.replace
    attempts = 0
    delays: list[float] = []

    def replace_with_lock(source: Path, destination: Path) -> None:
        """Simulate two transient locks without changing the destination."""
        nonlocal attempts
        attempts += 1
        if winerror is not None and attempts <= 2:
            error = PermissionError(errno.EACCES, "Environment temporarily locked")
            error.winerror = winerror
            raise error
        replace(source, destination)

    monkeypatch.setattr(os, "replace", replace_with_lock)
    monkeypatch.setattr(time, "sleep", delays.append)
    (tmp_path / "config").mkdir()
    if database_directory:
        (tmp_path / "database").mkdir()
        (tmp_path / "database" / "database.sqlite").write_bytes(b"existing database")
    (tmp_path / ".env.example").write_text(
        'APP_NAME="Orionis"\nAPP_KEY=\nFILESYSTEM_DISK=local\n'
        "DB_CONNECTION=sqlite\nDB_DATABASE=database/database.sqlite\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "database.py").write_text(
        'default = Env.get("DB_CONNECTION")\n'
        'service = Env.get("DB_SERVICE_NAME")\n'
        "connections = Connections(sqlite=None, mysql=None, pgsql=None, "
        "oracle=None, sqlserver=None, redshift=None)\n",
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
    assert delays == ([] if winerror is None else [0.1, 0.2])
    assert not (tmp_path / ".env.example").exists()
    if database == Database.SQLITE:
        assert values["DB_DATABASE"] == "database/database.sqlite"
        assert (tmp_path / "database").is_dir()
        if database_directory:
            assert (tmp_path / "database" / "database.sqlite").read_bytes() == b"existing database"
        else:
            assert not (tmp_path / "database" / "database.sqlite").exists()
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
    winerror: int | None,
    expected_attempts: int,
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

@pytest.mark.parametrize("filename", [".env.example", "env.example", "both"])
@pytest.mark.parametrize("database", [Database.SQLITE, Database.PGSQL, Database.ORACLE])
def test_environment_adds_missing_sections_and_retains_reference(
    tmp_path: Path,
    filename: str,
    database: Database,
) -> None:
    """Configure inherited defaults and remove only successfully consumed examples.

    Parameters
    ----------
    tmp_path : Path
        Isolated application directory.
    filename : str
        Preferred, legacy, or duplicate equivalent template filenames.
    database : Database
        Database selection requiring inherited core configuration.
    """
    original = (
        "APP_NAME=Template\nAPP_KEY=\n\nCACHE_STORE=file\n\n"
        "LOG_CHANNEL=single\n\nQUEUE_CONNECTION=database\n\nREALTIME_HOST=127.0.0.1\n"
    )
    examples = (".env.example", "env.example") if filename == "both" else (filename,)
    for example in examples:
        (tmp_path / example).write_text(original, encoding="utf-8")
    reference = b"# All settings\r\nAPP_NAME=reference\r\nS3_BUCKET=\r\n"
    (tmp_path / ".env.reference").write_bytes(reference)
    plan = InstallationPlan(
        name="example-app",
        path=tmp_path,
        storage=Storage.S3,
        database=database,
    )

    configure_environment(tmp_path, plan)

    path = tmp_path / ".env"
    result = path.read_text(encoding="utf-8")
    values = read_env(path)
    assert values["FILESYSTEM_DISK"] == "s3"
    assert values["DB_CONNECTION"] == database.value
    assert literal_value(values["APP_NAME"]) == "example-app"
    assert result.index("APP_KEY=") < result.index("DB_CONNECTION=")
    assert result.index("DB_DATABASE=") < result.index("CACHE_STORE=")
    assert result.index("CACHE_STORE=") < result.index("FILESYSTEM_DISK=")
    assert result.index("FILESYSTEM_DISK=") < result.index("LOG_CHANNEL=")
    assert "\n\nCACHE_STORE=" in result
    assert "\n\nFILESYSTEM_DISK=" in result
    assert "\n\nLOG_CHANNEL=" in result
    assert (tmp_path / ".env.reference").read_bytes() == reference
    assert not (tmp_path / ".env.example").exists()
    assert not (tmp_path / "env.example").exists()
    assert not list(tmp_path.glob(".tmp_*"))

@pytest.mark.parametrize(
    "storage_key",
    ["S3_KEY", "AWS_ACCESS_KEY_ID", "AZURE_ACCOUNT_KEY", "GCS_KEY_FILE"],
)
def test_followup_credentials_preserve_groups_and_original_content(
    tmp_path: Path,
    storage_key: str,
) -> None:
    """Keep connection updates grouped without rewriting unrelated dotenv content.

    Parameters
    ----------
    tmp_path : Path
        Isolated directory containing an environment file.
    storage_key : str
        Provider-specific credential belonging to the storage block.
    """
    path = tmp_path / ".env"
    multiline = 'CUSTOM_TEXT="first\r\nsecond # kept"\r\n'
    original = (
        "# Local settings\r\nAPP_NAME=demo\r\nAPP_KEY=\r\n\r\n"
        "# Database\r\nexport DB_CONNECTION=sqlite # selected driver\r\n"
        "DB_DATABASE='database/database.sqlite'\r\n\r\n"
        "# Storage\r\nFILESYSTEM_DISK=local\r\nS3_BUCKET=uploads\r\n\r\n"
        "# Logs\r\nLOG_CHANNEL=single\r\n\r\n" + multiline + "MANUAL_VALUE=${APP_NAME}\r\n"
    )
    path.write_bytes(original.encode("utf-8"))
    secret = "contraseña 'quoted' ${APP_NAME} \\ # literal\nsecond line"

    set_literal_env(path, "DB_PASSWORD", secret)
    set_env_value(path, "DB_PORT", "5432", quote_mode="never")
    set_env_value(path, "DB_CONNECTION", "pgsql")
    set_literal_env(path, storage_key, secret)

    result = path.read_bytes().decode("utf-8")
    values = read_env(path)
    assert literal_value(values["DB_PASSWORD"]) == secret
    assert literal_value(values[storage_key]) == secret
    assert values["DB_PORT"] == "5432"
    assert values["MANUAL_VALUE"] == "${APP_NAME}"
    assert "export DB_CONNECTION='pgsql' # selected driver\r\n" in result
    assert "DB_DATABASE='database/database.sqlite'\r\n" in result
    assert result.index("DB_DATABASE=") < result.index("DB_PASSWORD=")
    assert result.index("DB_PASSWORD=") < result.index("DB_PORT=")
    assert result.index("DB_PORT=") < result.index("# Storage")
    assert result.index("S3_BUCKET=") < result.index(storage_key + "=")
    assert result.index(storage_key + "=") < result.index("# Logs")
    assert multiline in result
    assert result.startswith("# Local settings\r\nAPP_NAME=demo\r\nAPP_KEY=\r\n\r\n")
    assert result.count("\n") == result.count("\r\n")

@pytest.mark.parametrize(
    ("raw", "comment"),
    [
        ("APP=plain  # comment\r\n", "# comment"),
        ("APP=plain\t# comment\n", "# comment"),
        ("APP=plain#literal\n", ""),
        ("APP=plain \v#literal\n", ""),
        ("APP=plain\r\n# next line\n", ""),
        ("APP= # comment\n", "# comment"),
        ("APP=#literal\n", ""),
        ('APP="text # inside" # comment\n', "# comment"),
        ("APP=plain # first # second\n", "# first # second"),
        pytest.param("APP=plain" + " " * 100_000 + "tail\n", "", id="long-whitespace"),
    ],
)
def test_inline_comments_preserve_dotenv_boundaries(raw: str, comment: str) -> None:
    """Keep literal hashes and line boundaries distinct from trailing comments.

    Parameters
    ----------
    raw : str
        Original dotenv assignment, including long whitespace without a comment.
    comment : str
        Expected trailing comment, or an empty string when absent.
    """
    assert _inline_comment(raw) == comment

def test_missing_block_precedes_next_section_comment(tmp_path: Path) -> None:
    """Keep section comments attached when inserting a previously absent block.

    Parameters
    ----------
    tmp_path : Path
        Isolated directory containing an environment file.
    """
    path = tmp_path / ".env"
    path.write_text("APP_KEY=\n\n# Logging settings\nLOG_CHANNEL=single\n", encoding="utf-8")

    set_env_value(path, "FILESYSTEM_DISK", "local")

    assert path.read_text(encoding="utf-8") == (
        "APP_KEY=\n\nFILESYSTEM_DISK='local'\n\n# Logging settings\nLOG_CHANNEL=single\n"
    )

@pytest.mark.parametrize("existing_environment", [False, True])
def test_generation_failure_preserves_templates_and_previous_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_environment: bool,
) -> None:
    """Retain templates and existing secrets when an atomic replacement fails.

    Parameters
    ----------
    tmp_path : Path
        Isolated directory containing template inputs.
    monkeypatch : pytest.MonkeyPatch
        Scoped operating-system failure injection.
    existing_environment : bool
        Whether a prior environment must remain byte-for-byte intact.
    """
    original = b"APP_NAME=Template\nAPP_KEY=\n"
    reference = b"# reference\nDB_CONNECTION=sqlite\n"
    (tmp_path / ".env.example").write_bytes(original)
    (tmp_path / ".env.reference").write_bytes(reference)
    path = tmp_path / ".env"
    if existing_environment:
        path.write_bytes(b"APP_KEY=existing-secret\n")

    def deny_replacement(source: Path, destination: Path) -> None:
        """Fail before replacing any environment content."""
        raise OSError(errno.ENOSPC, "No storage available")

    monkeypatch.setattr(os, "replace", deny_replacement)
    with pytest.raises(OSError):
        configure_environment(tmp_path, InstallationPlan(name="example-app", path=tmp_path))

    assert (tmp_path / ".env.example").read_bytes() == original
    assert (tmp_path / ".env.reference").read_bytes() == reference
    if existing_environment:
        assert path.read_bytes() == b"APP_KEY=existing-secret\n"
    else:
        assert not path.exists()
    assert not list(tmp_path.glob(".tmp_*"))

@pytest.mark.parametrize("database", ["", "../unsafe.sqlite", ":memory:"])
def test_invalid_sqlite_template_does_not_publish_environment(
    tmp_path: Path,
    database: str,
) -> None:
    """Validate template database paths before publishing the environment.

    Parameters
    ----------
    tmp_path : Path
        Isolated application directory.
    database : str
        Invalid SQLite filename provided by the template.
    """
    original = f"APP_NAME=Template\nAPP_KEY=\nDB_DATABASE={database}\n"
    (tmp_path / ".env.example").write_text(original, encoding="utf-8")

    with pytest.raises(CompatibilityError):
        configure_environment(tmp_path, InstallationPlan(name="example-app", path=tmp_path))

    assert not (tmp_path / ".env").exists()
    assert (tmp_path / ".env.example").read_text(encoding="utf-8") == original

@pytest.mark.parametrize(
    "key",
    [
        "DB_PASSWORD\nAPP_KEY",
        "export KEY",
        "KEY=bad",
        "",
        "1APP",
        "\u00c5PP",
        "APP_\u00e9",
        "APP_\u0661",
    ],
)
def test_invalid_environment_key_does_not_change_file(tmp_path: Path, key: str) -> None:
    """Reject malformed keys before writing an environment file.

    Parameters
    ----------
    tmp_path : Path
        Isolated application directory.
    key : str
        Invalid or nonportable key that must not become an assignment.
    """
    path = tmp_path / ".env"
    original = b"APP_KEY=existing\n"
    path.write_bytes(original)

    with pytest.raises(ValueError, match="Invalid environment key"):
        set_literal_env(path, key, "secret")

    assert path.read_bytes() == original

@pytest.mark.parametrize("key", ["_", "APP9", "_APP_1"])
def test_portable_environment_keys_are_supported(tmp_path: Path, key: str) -> None:
    """Accept ASCII letters, digits, and underscores in portable environment keys.

    Parameters
    ----------
    tmp_path : Path
        Isolated directory containing an empty environment file.
    key : str
        Valid ASCII key with an alphabetic or underscore first character.
    """
    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")

    set_env_value(path, key, "value")

    assert read_env(path)[key] == "value"
