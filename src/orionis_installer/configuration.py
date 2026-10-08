import ast
import base64
import shutil
from pathlib import Path, PureWindowsPath
import tomlkit
from dotenv import dotenv_values, set_key
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from orionis_installer.exceptions import CompatibilityError
from orionis_installer.messages import MESSAGES
from orionis_installer.models import Database, InstallationPlan

PORTS = {
    Database.MYSQL: 3306,
    Database.PGSQL: 5432,
    Database.ORACLE: 1521,
    Database.SQLSERVER: 1433,
    Database.REDSHIFT: 5439,
}

def orionis_requirement(document: tomlkit.TOMLDocument) -> Requirement:
    """
    Extract the template's single registry-based Orionis requirement.

    Parameters
    ----------
    document : tomlkit.TOMLDocument
        Parsed application manifest containing project dependencies.

    Returns
    -------
    Requirement
        Orionis requirement with the template's constraints and markers.

    Raises
    ------
    CompatibilityError
        If dependencies are invalid, omit or duplicate Orionis, or use a direct URL.
    """
    dependencies = document.get("project", {}).get("dependencies", [])
    matches: list[Requirement] = []
    try:
        for dependency in dependencies:
            requirement = Requirement(str(dependency))
            if canonicalize_name(requirement.name) == "orionis":
                matches.append(requirement)
    except InvalidRequirement as exc:
        raise CompatibilityError(MESSAGES["requirement_invalid"]) from exc
    if len(matches) != 1:
        raise CompatibilityError(MESSAGES["requirement_ambiguous"])
    if matches[0].url:
        raise CompatibilityError(MESSAGES["requirement_url_unsupported"])
    return matches[0]

def configure_pyproject( # NOSONAR
    root: Path, plan: InstallationPlan, *, python_version: str = "3.14.0"
) -> Requirement:
    """Apply application metadata and extras while preserving template constraints.

    Parameters
    ----------
    root : Path
        Staging directory containing the template's manifest.
    plan : InstallationPlan
        Validated application metadata and driver selections.
    python_version : str, optional
        Selected interpreter version used to evaluate constraints and markers.

    Returns
    -------
    Requirement
        Updated Orionis requirement written to the application manifest.

    Raises
    ------
    CompatibilityError
        If the manifest rejects the interpreter or redirects dependency resolution.
    """
    path = root / "pyproject.toml"
    try:
        document = tomlkit.parse(path.read_text(encoding="utf-8"))
        requirement = orionis_requirement(document)
        requires_python = document.get("project", {}).get("requires-python")
        if not requires_python or not SpecifierSet(str(requires_python)).contains(python_version):
            raise CompatibilityError(MESSAGES["skeleton_python_unsupported"])
        if requirement.marker and not requirement.marker.evaluate(
            {"python_version": "3.14", "python_full_version": python_version}
        ):
            raise CompatibilityError(MESSAGES["framework_marker_unsupported"])
        requirement.extras |= set(plan.extras)
        project = document["project"]
        original_authors = list(project.get("authors", []))
        project["name"] = plan.name
        project["description"] = plan.description
        author = {}
        if plan.author_name:
            author["name"] = plan.author_name
        if plan.author_email:
            author["email"] = plan.author_email
        if author:
            project["authors"] = [author]
        elif "authors" in project:
            del project["authors"]
        # Remove only identifiable inherited ownership; preserve unrelated template data.
        if original_authors and list(project.get("maintainers", [])) == original_authors:
            del project["maintainers"]
        if "urls" in project:
            for key, value in list(project["urls"].items()): # NOSONAR
                if str(value).rstrip("/") in {
                    "https://github.com/orionis-framework/framework",
                    "https://github.com/orionis-framework/skeleton",
                    "https://orionis-framework.com",
                }:
                    del project["urls"][key]
        for index, dependency in enumerate(project["dependencies"]):
            if canonicalize_name(Requirement(str(dependency)).name) == "orionis":
                project["dependencies"][index] = str(requirement)
        if "tool" not in document:
            document["tool"] = tomlkit.table()
        if "uv" not in document["tool"]:
            document["tool"]["uv"] = tomlkit.table()
        uv = document["tool"]["uv"]
        if "workspace" in uv or "sources" in uv:
            raise CompatibilityError(MESSAGES["skeleton_dependency_redirect"])
        uv["package"] = False
        path.write_text(tomlkit.dumps(document), encoding="utf-8")
    except (
        KeyError,
        TypeError,
        ValueError,
        InvalidSpecifier,
        tomlkit.exceptions.ParseError,
    ) as exc:
        raise CompatibilityError(MESSAGES["manifest_incompatible"]) from exc
    (root / ".python-version").write_text("3.14\n", encoding="ascii")
    return requirement

def environment_example(root: Path) -> Path:
    """
    Select an unambiguous environment example from the template.

    Parameters
    ----------
    root : Path
        Template directory containing an environment example.

    Returns
    -------
    Path
        Preferred dot-prefixed example or the supported legacy filename.

    Raises
    ------
    CompatibilityError
        If neither example exists or both examples have different contents.
    """
    preferred, legacy = root / ".env.example", root / "env.example"
    if preferred.is_file():
        if legacy.is_file() and preferred.read_bytes() != legacy.read_bytes():
            raise CompatibilityError(MESSAGES["environment_example_ambiguous"])
        return preferred
    if legacy.is_file():
        return legacy
    raise CompatibilityError(MESSAGES["environment_example_missing"])

def config_contract(root: Path, plan: InstallationPlan) -> None:
    """
    Verify selected drivers by inspecting downloaded configuration syntax.

    Parameters
    ----------
    root : Path
        Template directory containing storage and database configuration.
    plan : InstallationPlan
        Effective storage disk and database connection to verify.

    Raises
    ------
    CompatibilityError
        If syntax is unreadable or lacks the verified environment keys and drivers.
    """
    for filename, env_key, factory, active in (
        ("filesystems.py", "FILESYSTEM_DISK", "Disks", plan.active_storage.value),
        ("database.py", "DB_CONNECTION", "Connections", plan.active_database.value),
    ):
        try:
            tree = ast.parse((root / "config" / filename).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeError) as exc:
            raise CompatibilityError(MESSAGES["skeleton_configuration_unreadable"]) from exc
        keys = {
            n.args[0].value
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "Env"
            and n.func.attr == "get"
            and n.args
            and isinstance(n.args[0], ast.Constant)
        }
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == factory
        ]
        drivers = {kw.arg for call in calls for kw in call.keywords}
        # Redshift is inherited from the real framework Connections dataclass; verified
        # again in the child after uv resolution. No translation to PostgreSQL.
        inherited_redshift = active == "redshift" and any(
            isinstance(n, ast.ImportFrom)
            and n.module == "orionis.foundation.config.database"
            and any(alias.name == "Connections" for alias in n.names)
            for n in ast.walk(tree)
        )
        if env_key not in keys or (active not in drivers and not inherited_redshift):
            raise CompatibilityError(
                MESSAGES["skeleton_selection_unsupported"].format(active=active)
            )

def set_literal_env(path: Path, key: str, value: str) -> None:
    """
    Encode a literal environment value with Orionis' verified base64 type.

    Parameters
    ----------
    path : Path
        Environment file to update without loading it into the installer process.
    key : str
        Environment key receiving the encoded value.
    value : str
        Literal UTF-8 text to preserve through dotenv interpolation.
    """
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    set_key(path, key, f"base64:{encoded}", quote_mode="always", encoding="utf-8")

def read_env(path: Path) -> dict[str, str | None]:
    """
    Read environment entries without interpolation or process mutation.

    Parameters
    ----------
    path : Path
        Environment file to parse.

    Returns
    -------
    dict[str, str | None]
        Parsed values, including None for keys without an assigned value.
    """
    return dict(dotenv_values(path, interpolate=False, encoding="utf-8"))

def literal_value(value: str | None) -> str:
    """
    Decode supported Orionis literal types without evaluating their contents.

    Parameters
    ----------
    value : str or None
        Raw dotenv value, optionally prefixed with base64: or str:.

    Returns
    -------
    str
        Decoded literal text, or an empty string for a missing value.

    Raises
    ------
    CompatibilityError
        If a base64 value is malformed or does not encode UTF-8 text.
    """
    if value is None:
        return ""
    if value.startswith("base64:"):
        try:
            return base64.b64decode(value[7:], validate=True).decode("utf-8") # NOSONAR
        except ValueError, UnicodeError: # NOSONAR
            raise CompatibilityError(MESSAGES["environment_base64_invalid"]) from None
    if value.startswith("str:"):
        return value[4:]
    return value

def valid_sqlite_path(database: str) -> bool:
    """
    Check that SQLite names a persistent local file rather than memory or a URI.

    Parameters
    ----------
    database : str
        Decoded SQLite database filename declared by the application.

    Returns
    -------
    bool
        Whether the path is relative, persistent, and portable across supported platforms.
    """
    path = Path(database)
    normalized = database.strip().lower()
    return (
        bool(normalized)
        and bool(path.name)
        and normalized != ":memory:"
        and not normalized.startswith(("file:", "sqlite:", "sqlite+aiosqlite:"))
        and not path.anchor
        and not PureWindowsPath(database).drive
        and "\\" not in database
        and ".." not in path.parts
        and not any(ord(character) < 32 or ord(character) == 127 for character in database)
    )

def _validate_sqlite_database(root: Path, database: str) -> None:
    """Require a persistent SQLite file inside an existing application directory.

    Parameters
    ----------
    root : Path
        Application root containing the database directory.
    database : str
        Decoded relative SQLite filename.

    Raises
    ------
    CompatibilityError
        If the path is unsafe, names a directory, or has a missing parent.
    """
    if not valid_sqlite_path(database) or (root / database).is_dir():
        raise CompatibilityError(MESSAGES["sqlite_path_invalid"])
    if not (root / database).parent.is_dir():
        raise CompatibilityError(MESSAGES["sqlite_directory_missing"])


def configure_environment(root: Path, plan: InstallationPlan) -> None:
    """Create a local environment with verified drivers and pending credentials.

    Parameters
    ----------
    root : Path
        Staging directory containing configuration and an environment example.
    plan : InstallationPlan
        Application name and effective storage and database selections.

    Raises
    ------
    CompatibilityError
        If required configuration keys are missing or the SQLite path is unsafe.
    """
    config_contract(root, plan)
    example = environment_example(root)
    values = read_env(example)
    required = {"APP_NAME", "APP_KEY", "FILESYSTEM_DISK", "DB_CONNECTION", "DB_DATABASE"}
    if not required <= values.keys():
        raise CompatibilityError(MESSAGES["environment_keys_missing"])
    path = root / ".env"
    shutil.copyfile(example, path)
    path.chmod(0o600)
    set_literal_env(path, "APP_NAME", plan.name)
    set_key(path, "FILESYSTEM_DISK", plan.active_storage.value, quote_mode="always")
    set_key(path, "DB_CONNECTION", plan.active_database.value, quote_mode="always")
    if plan.active_database == Database.SQLITE:
        _validate_sqlite_database(root, literal_value(values["DB_DATABASE"]))
        # SQLite creates the file on first migration; never truncate one from the template.
    else:
        set_literal_env(path, "DB_HOST", literal_value(values.get("DB_HOST")) or "127.0.0.1")
        set_literal_env(path, "DB_DATABASE", plan.name)
        set_literal_env(path, "DB_USERNAME", "configure-me")
        set_key(path, "DB_PORT", str(PORTS[plan.active_database]), quote_mode="never")
        if plan.active_database == Database.ORACLE:
            # Oracle uses a service/SID/DSN/TNS, not the shared DB_DATABASE string.
            config = (root / "config" / "database.py").read_text(encoding="utf-8")
            if '"DB_SERVICE_NAME"' not in config and "'DB_SERVICE_NAME'" not in config:
                raise CompatibilityError(MESSAGES["oracle_service_unsupported"])
            set_literal_env(path, "DB_SERVICE_NAME", "configure-me")
        # DB_PASSWORD remains absent/commented. Do not manufacture credentials.

def ensure_gitignore(root: Path) -> None:
    """
    Ignore local secrets and SQLite files while keeping uv.lock trackable.

    Parameters
    ----------
    root : Path
        Configured application directory containing .env and .gitignore.
    """
    path = root / ".gitignore"
    current = path.read_text(encoding="utf-8")
    # Final negation keeps the application's lockfile tracked even if template ignores it.
    sqlite_ignore = ""
    values = read_env(root / ".env")
    if values.get("DB_CONNECTION") == "sqlite":
        sqlite = literal_value(values.get("DB_DATABASE"))
        # Gitignore metacharacters in a template path must be escaped as data.
        escaped = "".join("\\" + c if c in "*?[]!#" else c for c in sqlite)
        sqlite_ignore = f"/{escaped}\n/{escaped}-*\n"
    path.write_text(
        current.rstrip() + "\n\n# Orionis Installer: local runtime and secrets\n"
        "/.env\n/.venv/\n/storage/logs/\n/storage/framework/\n"
        "/database/*.sqlite\n/database/*.sqlite-*\n" + sqlite_ignore + "!uv.lock\n",
        encoding="utf-8",
    )
