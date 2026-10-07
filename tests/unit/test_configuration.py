"""Offline configuration tests use a clearly identified synthetic template."""

import base64
import json
import shutil
from itertools import product
from pathlib import Path

import pytest
import tomlkit
from dotenv import dotenv_values
from packaging.requirements import Requirement

from orionis_installer import __version__
from orionis_installer.configuration import (
    config_contract,
    configure_environment,
    configure_pyproject,
    ensure_gitignore,
    environment_example,
    literal_value,
    orionis_requirement,
    read_env,
    set_literal_env,
    write_provenance,
)
from orionis_installer.exceptions import CompatibilityError
from orionis_installer.models import Database, InstallationPlan, Stack, Storage

FIXTURE = Path(__file__).parents[1] / "fixtures" / "skeleton"


@pytest.fixture
def template(tmp_path):
    """Copy the synthetic skeleton into an isolated staging directory.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.

    Returns
    -------
    Path
        Isolated staging directory containing the copied fixture.
    """
    root = tmp_path / "staging with spaces"
    shutil.copytree(FIXTURE, root)
    return root


def plan(root, **choices):
    """Build an installation plan targeting a sibling application directory.

    Parameters
    ----------
    root : Path
        Synthetic template directory used to derive the sibling destination.
    **choices : object
        Application metadata or driver selections passed to InstallationPlan.

    Returns
    -------
    InstallationPlan
        Validated application choices targeting the sibling destination.
    """
    return InstallationPlan("test-app", root.parent / "application", **choices)


@pytest.mark.parametrize(("storage", "database"), list(product(Storage, Database)))
def test_every_choice_is_reflected_in_dependency_and_environment(template, storage, database):
    """Verify that dependency extras and environment drivers reflect every selection.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    storage : Storage
        Storage SDK selection exercised by the parameterized case.
    database : Database
        Database driver selection exercised by the parameterized case.
    """
    choices = plan(template, storage=storage, database=database)
    requirement = configure_pyproject(template, choices)
    configure_environment(template, choices)
    assert requirement.extras == set(choices.extras)
    assert requirement.specifier == Requirement("orionis>=0.801.0").specifier
    values = read_env(template / ".env")
    assert values["FILESYSTEM_DISK"] == choices.active_storage.value
    assert values["DB_CONNECTION"] == choices.active_database.value
    assert values["FILESYSTEM_DISK"] not in {"all", "storage"}
    assert values["DB_CONNECTION"] not in {"all", "database"}
    assert literal_value(values["APP_NAME"]) == choices.name


def test_comments_versions_development_groups_and_unrelated_fields_are_preserved(template):
    """Verify that manifest edits preserve comments, versions, groups, and unrelated fields.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    document = tomlkit.parse((template / "pyproject.toml").read_text(encoding="utf-8"))
    document["project"]["dependencies"][0] = (
        'Orionis[mysql]>=0.801.0,<0.900.0; python_version >= "3.14"'
    )
    (template / "pyproject.toml").write_text(tomlkit.dumps(document), encoding="utf-8")
    requirement = configure_pyproject(
        template, plan(template, storage=Storage.S3, database=Database.PGSQL)
    )
    result = (template / "pyproject.toml").read_text(encoding="utf-8")
    document = tomlkit.parse(result)
    assert "retain this comment" in result
    assert document["project"]["version"] == "0.7.9"
    assert document["dependency-groups"]["dev"] == ["ruff>=0.16.8"]
    assert document["tool"]["fixture"]["keep"] == "application-specific-value"
    assert "httpx>=0.28" in document["project"]["dependencies"]
    assert document["tool"]["uv"]["package"] is False
    assert requirement.extras == {"factories", "mysql", "pgsql", "s3"}
    assert str(requirement.specifier) == "<0.900.0,>=0.801.0"
    assert requirement.marker is not None
    assert str(requirement.marker) == 'python_version >= "3.14"'
    assert (template / ".python-version").read_text(encoding="ascii") == "3.14\n"


def test_legitimate_application_urls_and_maintainers_are_preserved(template):
    """Verify preservation of application URLs and unrelated maintainers.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    path = template / "pyproject.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["project"]["urls"] = {"Support": "https://application.example.test/support"}
    document["project"]["maintainers"] = [{"name": "Application Maintainer"}]
    path.write_text(tomlkit.dumps(document), encoding="utf-8")
    configure_pyproject(template, plan(template))
    project = tomlkit.parse(path.read_text(encoding="utf-8"))["project"]
    assert project["urls"] == {"Support": "https://application.example.test/support"}
    assert project["maintainers"] == [{"name": "Application Maintainer"}]


@pytest.mark.parametrize(
    ("name", "email", "expected"),
    [
        (None, None, None),
        ("Renée 'Author'", None, [{"name": "Renée 'Author'"}]),
        (None, "user@example.com", [{"email": "user@example.com"}]),
        ("Renée", "user@example.com", [{"name": "Renée", "email": "user@example.com"}]),
    ],
)
def test_authors_are_optional_and_escaped_with_tomlkit(template, name, email, expected):
    """Verify optional author metadata and correct TOML escaping.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    name : str or None
        Optional author name containing Unicode or quote characters.
    email : str or None
        Optional author email to include in project metadata.
    expected : list[dict[str, str]] or None
        Expected authors field after manifest configuration.
    """
    path = template / "pyproject.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["project"]["authors"] = [{"name": "Template Author", "email": "template@example.com"}]
    path.write_text(tomlkit.dumps(document), encoding="utf-8")
    configure_pyproject(template, plan(template, author_name=name, author_email=email))
    result = tomlkit.parse(path.read_text(encoding="utf-8"))["project"]
    assert result.get("authors") == expected
    assert "Template Author" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "dependencies",
    [
        [],
        ["httpx>=0.28"],
        ["orionis>=0.801", "Orionis[factories]>=0.802"],
        ["orionis>==oops"],
        ["orionis @ https://example.test/orionis.whl"],
    ],
)
def test_ambiguous_invalid_or_external_orionis_requirements_fail(dependencies):
    """Verify rejection of missing, duplicate, malformed, and URL-based Orionis requirements.

    Parameters
    ----------
    dependencies : list[str]
        Manifest dependencies with missing, duplicate, or invalid Orionis entries.
    """
    document = tomlkit.document()
    document["project"] = {"dependencies": dependencies}
    with pytest.raises(CompatibilityError):
        orionis_requirement(document)


def test_orionis_marker_excluding_target_python_is_rejected(template):
    """Verify rejection of an Orionis marker that excludes the target Python version.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    path = template / "pyproject.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["project"]["dependencies"][0] = 'orionis>=0.801.0; python_version < "3.14"'
    path.write_text(tomlkit.dumps(document), encoding="utf-8")
    with pytest.raises(CompatibilityError, match="marker"):
        configure_pyproject(template, plan(template))


@pytest.mark.parametrize("python_constraint", ["<3.14", ">=3.15", "not a valid specifier"])
def test_application_constraint_excluding_target_python_fails_before_publication(
    template, python_constraint
):
    """Verify rejection of incompatible Python constraints before application publication.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    python_constraint : str
        Application Python constraint that excludes the target or is malformed.
    """
    path = template / "pyproject.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["project"]["requires-python"] = python_constraint
    path.write_text(tomlkit.dumps(document), encoding="utf-8")
    with pytest.raises(CompatibilityError):
        configure_pyproject(template, plan(template))
    assert not (template / ".env").exists()


def test_selected_python_patch_version_is_used_for_compatibility(template):
    """Verify that compatibility checks use the selected Python patch version.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    path = template / "pyproject.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["project"]["requires-python"] = ">=3.14.5,<3.15"
    document["project"]["dependencies"][0] = 'orionis>=0.801.0; python_full_version >= "3.14.5"'
    path.write_text(tomlkit.dumps(document), encoding="utf-8")
    configure_pyproject(template, plan(template), python_version="3.14.6")


@pytest.mark.parametrize("redirect", ["workspace", "sources"])
def test_template_cannot_redirect_dependency_resolution(template, redirect):
    """Verify rejection of template settings that redirect dependency resolution.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    redirect : str
        uv configuration key that would redirect dependency resolution.
    """
    path = template / "pyproject.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["tool"]["uv"][redirect] = {}
    path.write_text(tomlkit.dumps(document), encoding="utf-8")
    with pytest.raises(CompatibilityError):
        configure_pyproject(template, plan(template))


@pytest.mark.parametrize("filename", [".env.example", "env.example"])
def test_both_environment_example_conventions_are_supported(template, filename):
    """Verify support for both inspected environment example filenames.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    filename : str
        Supported environment example filename exercised by the case.
    """
    original = template / ".env.example"
    if filename != ".env.example":
        original.rename(template / filename)
    example = template / filename
    before = example.read_bytes()
    assert environment_example(template) == example
    configure_environment(template, plan(template))
    assert example.read_bytes() == before
    assert (template / ".env").is_file()
    assert read_env(template / ".env")["CUSTOM_VALUE"] == "literal # value"


def test_identical_examples_prefer_dotenv_name(template):
    """Verify preference for the dot-prefixed filename when both examples are identical.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    shutil.copyfile(template / ".env.example", template / "env.example")
    assert environment_example(template) == template / ".env.example"


def test_differing_examples_are_ambiguous(template):
    """Verify rejection of conflicting environment examples.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    (template / "env.example").write_text("APP_KEY=different\n", encoding="utf-8")
    with pytest.raises(CompatibilityError, match="ambiguous"):
        environment_example(template)


def test_missing_environment_example_is_not_fabricated(template):
    """Verify failure when the template lacks an environment example.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    (template / ".env.example").unlink()
    with pytest.raises(CompatibilityError):
        environment_example(template)
    assert not (template / ".env").exists()


@pytest.mark.parametrize(
    "value",
    [
        "Whitespace between words",
        " quotes ' and \" ",
        "# comment",
        "$ dollar ${APP_NAME}",
        r"C:\Windows\User\path",
        "Unicode Montréal 🐍",
        "True",
        "123",
        "null",
        "",
    ],
)
def test_literal_values_survive_dotenv_interpolation_without_global_environment(
    value, tmp_path, monkeypatch
):
    """Verify literal round trips through dotenv interpolation without process environment changes.

    Parameters
    ----------
    value : str
        Literal or control-containing text exercised by the parameterized case.
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    monkeypatch : pytest.MonkeyPatch
        Fixture replacing process state or external dependencies for the test.
    """
    path = tmp_path / ".env"
    path.write_text("VALUE=old\n", encoding="utf-8")
    monkeypatch.setenv("VALUE", "installer-global-marker")
    monkeypatch.setenv("APP_NAME", "unexpected-interpolation")
    set_literal_env(path, "VALUE", value)
    # Match framework's interpolating dotenv parser, then its verified base64 decoder.
    raw = dotenv_values(path, interpolate=True)["VALUE"]
    assert raw is not None and raw.startswith("base64:")
    assert base64.b64decode(raw[7:], validate=True).decode("utf-8") == value
    assert literal_value(raw) == value
    import os

    assert os.environ["VALUE"] == "installer-global-marker"


@pytest.mark.parametrize("value", ["base64:not%valid", "base64:/w=="])
def test_malformed_typed_values_fail_without_exposing_contents(value):
    """Verify rejection of malformed typed values without displaying their contents.

    Parameters
    ----------
    value : str
        Literal or control-containing text exercised by the parameterized case.
    """
    with pytest.raises(CompatibilityError) as captured:
        literal_value(value)
    assert value not in str(captured.value)


def test_existing_key_and_sqlite_database_are_preserved(template):
    """Verify preservation of the existing application key and SQLite database bytes.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    path = template / ".env.example"
    path.write_text(
        path.read_text(encoding="utf-8").replace("APP_KEY=", "APP_KEY='existing-secret-key'"),
        encoding="utf-8",
    )
    database = template / "database" / "database.sqlite"
    database.write_bytes(b"existing database bytes\x00")
    configure_environment(template, plan(template))
    assert read_env(template / ".env")["APP_KEY"] == "existing-secret-key"
    assert database.read_bytes() == b"existing database bytes\x00"
    assert "existing-secret-key" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "database_path",
    [
        "../foreign.sqlite",
        "/absolute.sqlite",
        "missing/db.sqlite",
        "",
        r"C:\outside.sqlite",
        "C:relative.sqlite",
        r"..\foreign.sqlite",
        r"\outside.sqlite",
        ":memory:",
        "file::memory:?cache=shared",
        "file:database/database.sqlite?mode=memory",
        "sqlite:///:memory:",
        "sqlite+aiosqlite:///:memory:",
    ],
)
def test_sqlite_paths_are_local_and_have_a_template_directory(template, database_path):
    """Verify that SQLite paths remain local and have an existing template directory.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    database_path : str
        SQLite filename or unsafe path exercised by the parameterized case.
    """
    path = template / ".env.example"
    from dotenv import set_key

    set_key(path, "DB_DATABASE", database_path, quote_mode="always")
    with pytest.raises(CompatibilityError):
        configure_environment(template, plan(template))


@pytest.mark.parametrize("database_path", [":memory:", "file::memory:?cache=shared"])
@pytest.mark.parametrize("encoding", ["raw", "str", "base64"])
def test_typed_sqlite_memory_paths_are_rejected_before_publication(
    template, database_path, encoding
):
    """Reject memory-backed SQLite even when the environment stores a typed literal.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    database_path : str
        In-memory SQLite marker or URI that cannot retain installed schema.
    encoding : str
        Raw, string-prefixed, or base64 encoding used by the environment value.
    """
    path = template / ".env.example"
    if encoding == "base64":
        set_literal_env(path, "DB_DATABASE", database_path)
    else:
        from dotenv import set_key

        value = f"str:{database_path}" if encoding == "str" else database_path
        set_key(path, "DB_DATABASE", value, quote_mode="always")
    with pytest.raises(CompatibilityError, match="SQLite requires a relative local path"):
        configure_environment(template, plan(template))
    assert not (plan(template).path).exists()


@pytest.mark.parametrize(
    ("database", "port"),
    [
        (Database.MYSQL, "3306"),
        (Database.PGSQL, "5432"),
        (Database.ORACLE, "1521"),
        (Database.SQLSERVER, "1433"),
        (Database.REDSHIFT, "5439"),
    ],
)
def test_external_choices_change_port_and_leave_credentials_pending(template, database, port):
    """Verify external driver ports and explicitly pending credentials.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    database : Database
        Database driver selection exercised by the parameterized case.
    port : str
        Database port text exercised by the parameterized case.
    """
    configure_environment(template, plan(template, database=database))
    values = read_env(template / ".env")
    assert values["DB_CONNECTION"] == database.value
    assert values["DB_PORT"] == port
    assert literal_value(values["DB_USERNAME"]) == "configure-me"
    assert "DB_PASSWORD" not in values


def test_oracle_service_is_explicitly_pending_and_not_inferred_from_application_name(template):
    """Verify that Oracle receives a pending service independent of the application name.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    configure_environment(template, plan(template, database=Database.ORACLE))
    values = read_env(template / ".env")
    assert literal_value(values["DB_SERVICE_NAME"]) == "configure-me"
    assert literal_value(values["DB_SERVICE_NAME"]) != "test-app"
    assert values["DB_PORT"] == "1521"
    assert "DB_PASSWORD" not in values


def test_missing_verified_oracle_service_mapping_is_rejected(template):
    """Verify rejection of Oracle configuration without the inspected service mapping.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    path = template / "config" / "database.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace("DB_SERVICE_NAME", "UNRELATED_SERVICE"),
        encoding="utf-8",
    )
    with pytest.raises(CompatibilityError, match="DB_SERVICE_NAME"):
        configure_environment(template, plan(template, database=Database.ORACLE))


def test_all_drivers_use_explicit_effective_defaults(template):
    """Verify that aggregate driver selections honor explicit effective defaults.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    choices = plan(
        template,
        storage=Storage.ALL,
        database=Database.ALL,
        default_storage=Storage.GCS,
        default_database=Database.REDSHIFT,
    )
    configure_environment(template, choices)
    values = read_env(template / ".env")
    assert values["FILESYSTEM_DISK"] == "gcs"
    assert values["DB_CONNECTION"] == "redshift"


def test_contract_requires_verified_active_disk(template):
    """Verify rejection of a storage disk absent from the inspected configuration.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    path = template / "config" / "filesystems.py"
    path.write_text(path.read_text(encoding="utf-8").replace(", gcs=GCS()", ""), encoding="utf-8")
    with pytest.raises(CompatibilityError, match="gcs"):
        config_contract(template, plan(template, storage=Storage.GCS))


def test_redshift_is_inherited_only_with_official_connections_import(template):
    """Verify Redshift inheritance only through the official Connections import.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    config_contract(template, plan(template, database=Database.REDSHIFT))
    path = template / "config" / "database.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "from orionis.foundation.config.database import ",
            "from unrelated import ",
        ),
        encoding="utf-8",
    )
    with pytest.raises(CompatibilityError, match="redshift"):
        config_contract(template, plan(template, database=Database.REDSHIFT))


def test_missing_verified_environment_key_fails(template):
    """Verify rejection of an environment example missing a required key.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    """
    path = template / ".env.example"
    path.write_text(path.read_text(encoding="utf-8").replace("APP_KEY=\n", ""), encoding="utf-8")
    with pytest.raises(CompatibilityError):
        configure_environment(template, plan(template))


@pytest.mark.parametrize("stack", list(Stack))
def test_provenance_has_no_authors_or_environment_secrets_and_lock_is_trackable(template, stack):
    """Verify that provenance excludes secrets and uv.lock remains trackable.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    stack : Stack
        Source stack recorded alongside its exact repository and branch.
    """
    ensure_gitignore(template)
    choices = plan(template, author_email="private@example.test", stack=stack)
    write_provenance(template, "a" * 40, choices)
    text = (template / ".orionis-install.json").read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["sha"] == "a" * 40
    assert data["installer"] == __version__
    assert data["stack"] == stack.value
    assert data["skeleton"] == choices.source.repository
    assert data["branch"] == choices.source.branch
    assert data["extras"] == ["factories"]
    assert "private@example.test" not in text
    assert "APP_KEY" not in text and "DB_PASSWORD" not in text
    ignore = (template / ".gitignore").read_text(encoding="utf-8")
    assert "/database/*.sqlite-*" in ignore
    assert ignore.rstrip().endswith("!uv.lock")


@pytest.mark.parametrize("database_path", ["database/application.db", "database/app[1]#file.db"])
def test_custom_sqlite_filename_and_auxiliaries_are_ignored(template, database_path):
    """Verify Git ignore entries for custom SQLite filenames and their auxiliary files.

    Parameters
    ----------
    template : Path
        Disposable copy of the explicitly synthetic skeleton fixture.
    database_path : str
        SQLite filename or unsafe path exercised by the parameterized case.
    """
    from dotenv import set_key

    set_key(template / ".env.example", "DB_DATABASE", database_path, quote_mode="always")
    configure_environment(template, plan(template))
    ensure_gitignore(template)
    ignore = (template / ".gitignore").read_text(encoding="utf-8")
    expected = "/" + "".join(
        "\\" + character if character in "*?[]!#" else character for character in database_path
    )
    assert expected + "\n" in ignore
    assert expected + "-*\n" in ignore
    assert ignore.rstrip().endswith("!uv.lock")
