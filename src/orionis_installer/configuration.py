import ast
import base64
import io
import os
import re
import tempfile
from pathlib import Path, PureWindowsPath
import tomlkit
from dotenv import dotenv_values
from dotenv.parser import Binding, parse_stream
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from orionis_installer.exceptions import CompatibilityError
from orionis_installer.messages import MESSAGES
from orionis_installer.models import Database, InstallationPlan
from orionis_installer.skeleton import WINDOWS_LOCK_ATTEMPTS, wait_for_windows_lock

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


def configure_pyproject(  # NOSONAR
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
            for key, value in list(project["urls"].items()):  # NOSONAR
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
    Verify explicit driver overrides, allowing inherited framework defaults.

    Parameters
    ----------
    root : Path
        Template directory optionally containing storage and database overrides.
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
        path = root / "config" / filename
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
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


_ENV_GROUPS = (
    "APP",
    "AUTH",
    "HTTP",
    "DB",
    "CACHE",
    "FILESYSTEM",
    "LOG",
    "QUEUE",
    "SESSION",
    "MAIL",
    "REALTIME",
    "MCP",
    "TASKS",
    "VIEW",
    "TESTING",
)
_STORAGE_PREFIXES = {"FILESYSTEM", "LOCAL", "PUBLIC", "S3", "AWS", "AZURE", "GCS"}


def _environment_group(key: str) -> str:
    """Resolve the environment block shared by related service settings.

    Parameters
    ----------
    key : str
        Environment variable whose service block must be located.

    Returns
    -------
    str
        Shared service name, including storage provider and database-backed aliases.
    """
    prefix = key.partition("_")[0]
    if prefix in _STORAGE_PREFIXES:
        return "FILESYSTEM"
    if key.startswith("DB_CACHE_"):
        return "CACHE"
    if key.startswith("DB_TASK_"):
        return "TASKS"
    return prefix


def _quoted_value(value: str) -> str:
    """Quote dotenv text with double quotes unless single quotes avoid escaping.

    Parameters
    ----------
    value : str
        Parsed or caller-supplied text whose characters must remain unchanged.

    Returns
    -------
    str
        Delimited value with escaped backslashes and any unavoidable inner quotes.
    """
    quote = "'" if '"' in value and "'" not in value else '"'
    escaped = value.replace("\\", "\\\\").replace(quote, "\\" + quote)
    return quote + escaped + quote


def _assignment(key: str, value: str, quote_mode: str, newline: str) -> str:
    """Encode one assignment without interpreting its value or expanding references.

    Parameters
    ----------
    key : str
        Portable environment variable name.
    value : str
        Raw value or an already encoded Orionis literal.
    quote_mode : str
        Dotenv quoting policy: ``always``, ``auto``, or ``never``.
    newline : str
        Line ending used by the existing environment file.

    Returns
    -------
    str
        Complete assignment terminated by the requested line ending.

    Raises
    ------
    ValueError
        If the key or quoting policy is invalid.
    """
    if not re.fullmatch(r"[A-Za-z_]\w*", key, flags=re.ASCII):
        raise ValueError("Invalid environment key")
    if quote_mode not in {"always", "auto", "never"}:
        raise ValueError("Invalid environment quoting policy")
    if quote_mode == "always" or (quote_mode == "auto" and not value.isalnum()):
        value = _quoted_value(value)
    return f"{key}={value}{newline}"


def _inline_comment(raw: str) -> str:  # NOSONAR
    """Preserve a trailing comment outside an assignment's quoted value.

    Parameters
    ----------
    raw : str
        Original dotenv binding, possibly containing a quoted multiline value.

    Returns
    -------
    str
        Trailing comment beginning with ``#``, or an empty string if absent.
    """
    _head, separator, tail = raw.partition("=")
    if not separator:
        return ""
    leading = tail[: len(tail) - len(tail.lstrip(" \t"))]
    tail = tail.lstrip(" \t")
    if tail.startswith(("'", '"')):
        quote = tail[0]
        index = 1
        while index < len(tail):
            if tail[index] == "\\":
                index += 2
                continue
            if tail[index] == quote:
                return (
                    tail[index + 1 :].strip() if tail[index + 1 :].lstrip().startswith("#") else ""
                )
            index += 1
        return ""
    match = re.search(r"(?<=[ \t])#[^\r\n]*", tail)
    if match:
        return match.group()
    return tail.rstrip("\r\n") if leading and tail.startswith("#") else ""


def _normalize_environment_quotes(text: str) -> str:
    """Apply the quoting policy to template strings without altering their values.

    Parameters
    ----------
    text : str
        Template contents, including comments and optionally multiline values.

    Returns
    -------
    str
        Contents retaining unquoted values, duplicate bindings, and line endings.
    """
    output: list[str] = []
    for binding in parse_stream(io.StringIO(text)):
        raw = binding.original.string
        head, separator, tail = raw.partition("=")
        stripped = tail.lstrip(" \t")
        if binding.value is not None and stripped.startswith(("'", '"')):
            leading = tail[: len(tail) - len(stripped)]
            comment = _inline_comment(raw)
            ending = re.search(r"(?:\r\n|\r|\n)\Z", raw)
            raw = head + separator + leading + _quoted_value(binding.value)
            raw += (" " + comment if comment else "") + (ending.group() if ending else "")
        output.append(raw)
    return "".join(output)


def _updated_environment(text: str, key: str, value: str, quote_mode: str) -> str:  # NOSONAR
    """Replace a value in place or insert it into its existing service block.

    Parameters
    ----------
    text : str
        Original environment contents with their existing line endings.
    key : str
        Environment variable to add or replace.
    value : str
        Raw value or an already encoded Orionis literal.
    quote_mode : str
        Dotenv quoting policy passed to the assignment encoder.

    Returns
    -------
    str
        Updated contents retaining unrelated bindings, comments, and multiline values.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    assignment = _assignment(key, value, quote_mode, newline)
    bindings = list(parse_stream(io.StringIO(text)))
    matches = [binding for binding in bindings if binding.key == key]
    if matches:
        output: list[str] = []
        for binding in bindings:
            raw = binding.original.string
            if binding.key == key:
                prefix = re.match(r"\s*(?:export[^\S\r\n]+)?", raw)
                comment = _inline_comment(raw)
                raw = (prefix.group() if prefix else "") + assignment.rstrip("\r\n")
                raw += (" " + comment if comment else "") + newline
            output.append(raw)
        return "".join(output)

    group = _environment_group(key)
    ranks = {name: index for index, name in enumerate(_ENV_GROUPS)}
    rank = ranks.get(group, len(ranks))
    records: list[tuple[Binding, int, int, int]] = []
    cursor = previous_end = 0
    for binding in bindings:
        end = cursor + len(binding.original.string)
        if binding.key is not None:
            records.append((binding, cursor, end, previous_end))
            previous_end = end
        cursor = end
    siblings = [item for item in records if _environment_group(item[0].key or "") == group]
    if siblings:
        position = siblings[-1][2]
        before, after = text[:position], text[position:]
        return before + ("" if before.endswith(("\n", "\r")) else newline) + assignment + after
    for binding, _start, _end, block_start in records:
        other_rank = ranks.get(_environment_group(binding.key or ""), len(ranks))
        if other_rank > rank:
            before = text[:block_start].rstrip("\r\n")
            after = text[block_start:].lstrip("\r\n")
            return (before + newline * 2 if before else "") + assignment + newline + after
    before = text.rstrip("\r\n")
    return (before + newline * 2 if before else "") + assignment


def _write_environment(path: Path, text: str) -> None:
    """Atomically replace an environment file while retrying transient Windows locks.

    Parameters
    ----------
    path : Path
        Destination environment file in an existing directory.
    text : str
        Complete replacement contents to persist as UTF-8.

    Returns
    -------
    None
        Replace the destination only after its temporary file is completely written.

    Raises
    ------
    OSError
        If a write fails or a transient Windows lock persists after five attempts.
    """
    for attempt in range(WINDOWS_LOCK_ATTEMPTS):
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                prefix=".tmp_",
                dir=path.parent,
                delete=False,
            ) as target:
                temporary = Path(target.name)
                target.write(text)
                target.flush()
                os.fsync(target.fileno())
            temporary.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o600)
            os.replace(temporary, path)
        except PermissionError as exc:
            wait_for_windows_lock(exc, attempt)
        else:
            return
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def set_env_value(path: Path, key: str, value: str, *, quote_mode: str = "always") -> None:
    """Write a typed environment value inside its related configuration block.

    Parameters
    ----------
    path : Path
        Environment file to update without mutating the installer process.
    key : str
        Environment key to add or replace.
    value : str
        Raw value to store; use ``set_literal_env`` for user-supplied text.
    quote_mode : str, optional
        Dotenv quoting policy: ``always``, ``auto``, or ``never``.

    Returns
    -------
    None
        Atomically update the file without loading its values into the process.

    Raises
    ------
    PermissionError
        If transient Windows file locks persist after five attempts.
    ValueError
        If the key or quoting policy is invalid.
    """
    with path.open(encoding="utf-8", newline="") as source:
        text = source.read()
    _write_environment(path, _updated_environment(text, key, value, quote_mode))


def _set_env_key(path: Path, key: str, value: str, *, quote_mode: str = "always") -> None:
    """Keep internal callers on the shared grouped environment writer.

    Parameters
    ----------
    path : Path
        Environment file to update.
    key : str
        Environment variable to add or replace.
    value : str
        Raw value or an already encoded Orionis literal.
    quote_mode : str, optional
        Dotenv quoting policy used for the assignment.

    Returns
    -------
    None
        Delegate the atomic update to the common environment writer.
    """
    set_env_value(path, key, value, quote_mode=quote_mode)


def set_literal_env(path: Path, key: str, value: str) -> None:
    """
    Write readable dotenv text without Base64 or unintended Orionis type coercion.

    Parameters
    ----------
    path : Path
        Environment file to update without loading it into the installer process.
    key : str
        Environment key receiving the quoted text.
    value : str
        Text to store directly; references retain standard dotenv interpolation.
    """
    _set_env_key(path, key, _literal_text(value))


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
    if value.startswith("base64:"):  # NOSONAR
        try:
            return base64.b64decode(value[7:], validate=True).decode("utf-8")  # NOSONAR
        except ValueError, UnicodeError:  # NOSONAR
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


def _prepare_sqlite_database(root: Path, database: str) -> None:
    """Prepare the parent directory for a persistent application-local SQLite file.

    Parameters
    ----------
    root : Path
        Application root containing or receiving the database directory.
    database : str
        Decoded relative SQLite filename.

    Raises
    ------
    CompatibilityError
        If the path is unsafe or names a directory.
    OSError
        If the database's parent directory cannot be created.
    """
    if not valid_sqlite_path(database) or (root / database).is_dir():
        raise CompatibilityError(MESSAGES["sqlite_path_invalid"])
    (root / database).parent.mkdir(parents=True, exist_ok=True)


def _literal_text(value: str) -> str:
    """Keep text readable while preventing Orionis from coercing its string type.

    Parameters
    ----------
    value : str
        Caller-supplied text, possibly resembling a typed Orionis value.

    Returns
    -------
    str
        Plain text, with ``str:`` only for values Orionis would otherwise interpret.
    """
    if value.startswith(("base64:", "str:")) or value.lower() in {
        "true",
        "false",
        "none",
        "null",
        "nan",
        "nil",
    }:
        return "str:" + value
    try:
        ast.literal_eval(value)
    except ValueError, SyntaxError:
        return value
    return "str:" + value


def configure_environment(root: Path, plan: InstallationPlan) -> None:  # NOSONAR
    """Create a local environment and consume its successfully applied examples.

    Parameters
    ----------
    root : Path
        Staging directory containing configuration and an environment example.
    plan : InstallationPlan
        Application name and effective storage and database selections.

    Returns
    -------
    None
        Publish grouped settings, retain the reference, and remove consumed examples.

    Raises
    ------
    CompatibilityError
        If required configuration keys are missing or the SQLite path is unsafe.
    """
    config_contract(root, plan)
    example = environment_example(root)
    values = read_env(example)
    if not {"APP_NAME", "APP_KEY"} <= values.keys():
        raise CompatibilityError(MESSAGES["environment_keys_missing"])
    with example.open(encoding="utf-8", newline="") as source:
        text = _normalize_environment_quotes(source.read())
    updates = [
        ("APP_NAME", _literal_text(plan.name), "always"),
        ("FILESYSTEM_DISK", plan.active_storage.value, "always"),
        ("DB_CONNECTION", plan.active_database.value, "always"),
    ]
    if plan.active_database == Database.SQLITE:
        database = literal_value(values.get("DB_DATABASE", "database/database.sqlite"))
        _prepare_sqlite_database(root, database)
        if "DB_DATABASE" not in values:
            updates.append(("DB_DATABASE", database, "always"))
        # SQLite creates the file on first migration; never truncate template data.
    else:
        for key, value in (
            ("DB_HOST", literal_value(values.get("DB_HOST")) or "127.0.0.1"),
            ("DB_DATABASE", plan.name),
            ("DB_USERNAME", "configure-me"),
        ):
            updates.append((key, _literal_text(value), "always"))
        updates.append(("DB_PORT", str(PORTS[plan.active_database]), "never"))
        if plan.active_database == Database.ORACLE:
            database_config = root / "config" / "database.py"
            if database_config.exists():
                config = database_config.read_text(encoding="utf-8")
                if '"DB_SERVICE_NAME"' not in config and "'DB_SERVICE_NAME'" not in config:
                    raise CompatibilityError(MESSAGES["oracle_service_unsupported"])
            updates.append(("DB_SERVICE_NAME", "configure-me", "always"))
        # DB_PASSWORD remains absent/commented. Do not manufacture credentials.
    for key, value, quote_mode in updates:
        text = _updated_environment(text, key, value, quote_mode)
    _write_environment(root / ".env", text)
    (root / ".env").chmod(0o600)
    # Both examples were checked for ambiguity before configuration. Keep the reference.
    for filename in (".env.example", "env.example"):
        (root / filename).unlink(missing_ok=True)


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
