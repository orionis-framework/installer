import ast
import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from orionis_installer.configuration import (
    literal_value,
    read_env,
    set_literal_env,
    valid_sqlite_path,
)
from orionis_installer.exceptions import Cancelled, CompatibilityError, InstallerError
from orionis_installer.installer import project_python
from orionis_installer.messages import MESSAGES
from orionis_installer.models import (
    Database,
    InstallationResult,
    PostInstallOptions,
    State,
)
from orionis_installer.prerequisites import Prerequisites
from orionis_installer.processes import Runner, resolve_executable
from orionis_installer.validation import is_redirect, validate_text

class PostUI(Protocol):
    """Define the interaction required by optional post-install operations."""

    def confirm(self, label: str, default: bool) -> bool:
        """
        Request an explicit answer to a post-install confirmation.

        Parameters
        ----------
        label : str
            Confirmation question displayed to the user.
        default : bool
            Answer selected when the user accepts the default.

        Returns
        -------
        bool
            Whether the user accepts the requested operation.
        """
        ...

    def text(
        self,
        label: str,
        default: str = "",
        *,
        validator: Callable[[str], object] | None = None,
        password: bool = False,
    ) -> str:
        """
        Request connection text with optional validation and password masking.

        Parameters
        ----------
        label : str
            Connection field label displayed to the user.
        default : str, optional
            Suggested value for a non-secret field.
        validator : Callable[[str], object] or None, optional
            Validation callback applied to the supplied text.
        password : bool, optional
            Whether to mask the user's input.

        Returns
        -------
        str
            Submitted connection field value.
        """
        ...

    def message(self, value: str) -> None:
        """
        Display a post-install progress or connection message.

        Parameters
        ----------
        value : str
            Non-secret message to display.
        """
        ...

    def warning(self, value: str) -> None:
        """
        Display a post-install diagnostic without stopping independent work.

        Parameters
        ----------
        value : str
            Warning explaining a failed or deferred operation.
        """
        ...

def seeder_safety(root: Path) -> None: # NOSONAR
    """
    Inspect a seeder tree independently of schema migration execution.

    A generic AST pass cannot certify arbitrary executable code. This is a conservative
    guard that permits only empty modules, docstrings, and pass statements. Other
    seeder contracts require manual review before the user executes them separately.

    Parameters
    ----------
    root : Path
        Application directory whose seeder tree must be inspected recursively.

    Raises
    ------
    CompatibilityError
        If the seeder tree redirects paths, contains invalid syntax, or executes code.
    """
    seeders = root / "database" / "seeders"
    paths: list[Path] = []
    if seeders.exists():
        if is_redirect(seeders):
            raise CompatibilityError(MESSAGES["seeders_directory_redirect"])
        for current, directories, filenames in os.walk(seeders, followlinks=False):
            for name in (*directories, *filenames):
                path = Path(current) / name
                if is_redirect(path):
                    raise CompatibilityError(MESSAGES["seeders_tree_redirect"])
                if path.is_file() and path.suffix == ".py":
                    paths.append(path)
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError, UnicodeError:
            raise CompatibilityError(MESSAGES["seeders_unreadable"]) from None
        # Only empty/docstring/pass modules have a provably inert contract. An absence
        # of literal password dict keys does not prove a dynamic seeder safe.
        inert = all(
            isinstance(node, ast.Pass)
            or (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
            for node in tree.body
        )
        if not inert:
            raise CompatibilityError(MESSAGES["seeders_unsafe"])

def connection_values(root: Path) -> dict[str, str]:
    """
    Read verified connection keys without importing application configuration.

    Parameters
    ----------
    root : Path
        Application directory containing its local environment file.

    Returns
    -------
    dict[str, str]
        Decoded connection settings, with empty strings for missing keys.
    """
    values = read_env(root / ".env")
    return {
        key: literal_value(values.get(key))
        for key in (
            "DB_CONNECTION",
            "DB_HOST",
            "DB_PORT",
            "DB_DATABASE",
            "DB_USERNAME",
            "DB_PASSWORD",
            "DB_SERVICE_NAME",
            "DB_SID",
            "DB_DSN",
            "DB_TNS",
        )
    }

def valid_port(value: str) -> bool:
    """
    Check a bounded ASCII decimal port without unsafe integer conversion.

    Parameters
    ----------
    value : str
        Proposed database port text.

    Returns
    -------
    bool
        Whether the text represents a port between one and 65535.
    """
    return (
        1 <= len(value) <= 5 and value.isascii() and value.isdecimal() and 1 <= int(value) <= 65535
    )

def connection_ready(values: dict[str, str]) -> bool:
    """
    Check whether local or external connection settings are ready to use.

    Parameters
    ----------
    values : dict[str, str]
        Decoded connection keys returned by connection_values.

    Returns
    -------
    bool
        Whether SQLite names a persistent local file or external credentials are complete.
    """
    if values["DB_CONNECTION"] == Database.SQLITE.value:
        return valid_sqlite_path(values["DB_DATABASE"])
    server_ready = (
        all(
            values.get(key)
            for key in ("DB_HOST", "DB_PORT", "DB_DATABASE", "DB_USERNAME", "DB_PASSWORD")
        )
        and values["DB_USERNAME"] != "configure-me"
        and valid_port(values["DB_PORT"])
    )
    if values["DB_CONNECTION"] == Database.ORACLE.value:
        service = values.get("DB_SERVICE_NAME", "")
        return server_ready and service not in ("", "configure-me")
    return server_ready

def _connection_prompt(root: Path, ui: PostUI) -> bool:
    """
    Collect explicitly authorized connection fields into the local environment.

    Parameters
    ----------
    root : Path
        Application directory whose environment receives the connection settings.
    ui : PostUI
        Interface providing confirmation and protected password input.

    Returns
    -------
    bool
        Whether the user chose to configure and supplied all requested fields.

    Raises
    ------
    CompatibilityError
        If a required field is empty or the supplied port is invalid.
    """
    if not ui.confirm(MESSAGES["connection_configuration_prompt"], False):
        return False
    # Verified shared keys for the inspected drivers; .env never enters our environment.
    database_key = (
        "DB_SERVICE_NAME"
        if connection_values(root)["DB_CONNECTION"] == Database.ORACLE.value
        else "DB_DATABASE"
    )
    for key, label in (
        ("DB_HOST", MESSAGES["connection_host_label"]),
        ("DB_PORT", MESSAGES["connection_port_label"]),
        (
            database_key,
            MESSAGES["connection_oracle_service_label"]
            if database_key == "DB_SERVICE_NAME"
            else MESSAGES["connection_database_label"],
        ),
        ("DB_USERNAME", MESSAGES["connection_username_label"]),
        ("DB_PASSWORD", MESSAGES["connection_password_label"]),
    ):
        value = ui.text(label, password=key == "DB_PASSWORD")
        validate_text(value, label)
        if not value:
            raise CompatibilityError(MESSAGES["connection_incomplete"])
        if key == "DB_PORT":
            if not valid_port(value):
                raise CompatibilityError(MESSAGES["connection_port_invalid"])
            from dotenv import set_key

            set_key(root / ".env", key, value, quote_mode="never")
        else:
            set_literal_env(root / ".env", key, value)
    return True

def _safe_connection_label(value: str) -> str:
    """
    Hide connection labels that could contain credentials or terminal controls.

    Parameters
    ----------
    value : str
        Database, service, or host value proposed for display.

    Returns
    -------
    str
        Original label when displayable, or the configured hidden-value message.
    """
    # Connection display is deliberately narrower than valid credential syntax.
    if any(c in value for c in "@\x1b\r\n") or "://" in value:
        return MESSAGES["connection_display_hidden"]
    return value

def _migrate(result: InstallationResult, runner: Runner, ui: PostUI, no_interaction: bool) -> State:
    """
    Apply schema migrations with the verified application's own interpreter.

    Reactor only runs seeders when its separate ``--seed`` option is supplied.
    Schema migrations therefore do not require inspecting or executing seeders.

    Parameters
    ----------
    result : InstallationResult
        Published application and selected database connection.
    runner : Runner
        Process runner executing the application's own interpreter.
    ui : PostUI
        Interface displaying the connection and obtaining pending credentials.
    no_interaction : bool
        Whether missing credentials must fail without prompting.

    Returns
    -------
    State
        Completed migration state, or skipped when connection setup is declined.

    Raises
    ------
    CompatibilityError
        If the effective connection is changed or incomplete, or Python is unavailable.
    InstallerError
        If the application's migration command fails.
    """
    root = result.plan.path
    values = connection_values(root)
    if values["DB_CONNECTION"] != result.plan.active_database.value:
        raise CompatibilityError(MESSAGES["connection_changed"])
    if not connection_ready(values):
        if values["DB_CONNECTION"] == Database.SQLITE.value:
            raise CompatibilityError(MESSAGES["sqlite_path_invalid"])
        if no_interaction:
            raise CompatibilityError(MESSAGES["non_interactive_connection_incomplete"])
        if not _connection_prompt(root, ui):
            return State.SKIPPED
        values = connection_values(root)
        if not connection_ready(values):
            raise CompatibilityError(MESSAGES["connection_invalid"])
    database_name = (
        values["DB_SERVICE_NAME"]
        if values["DB_CONNECTION"] == Database.ORACLE.value
        else values["DB_DATABASE"]
    )
    label = MESSAGES["connection_display"].format(
        connection=values["DB_CONNECTION"], database=_safe_connection_label(database_name)
    )
    if values["DB_CONNECTION"] != Database.SQLITE.value:
        label += MESSAGES["connection_display_host"].format(
            host=_safe_connection_label(values["DB_HOST"])
        )
    ui.message(label + MESSAGES["migration_data_warning"])
    # Exact equivalent of `uv run --no-sync python -B reactor migrate`,
    # without further syncing or selecting an interpreter from uvx/PATH.
    runner.run([project_python(root), "-B", "reactor", "migrate"], cwd=root, timeout=300)
    return State.COMPLETED

def run_post_install( # NOSONAR
    result: InstallationResult,
    options: PostInstallOptions,
    prerequisites: Prerequisites,
    runner: Runner,
    ui: PostUI,
    *,
    no_interaction: bool,
) -> None:
    """Execute authorized follow-up operations and record their independent outcomes.

    Parameters
    ----------
    result : InstallationResult
        Published application's mutable operation states and warning collection.
    options : PostInstallOptions
        Explicit choices or unset values governed by interaction policy.
    prerequisites : Prerequisites
        Verified Git executable used for repository initialization.
    runner : Runner
        Process runner for Git, Reactor, and the editor launcher.
    ui : PostUI
        Interface for confirmations, connection input, and diagnostics.
    no_interaction : bool
        Whether unset choices are skipped without prompting.

    Raises
    ------
    Cancelled
        If the user interrupts a prompt or an operation.
    """
    for attribute, flag, question, default in (
        ("git", options.git, MESSAGES["post_git_prompt"], True),
        (
            "migrations",
            options.migrate,
            MESSAGES["post_migration_prompt"],
            False,
        ),
        ("editor", options.open, MESSAGES["post_editor_prompt"], True),
    ):
        try:
            selected = (
                flag
                if flag is not None
                else (False if no_interaction else ui.confirm(question, default)) # NOSONAR
            )
            if not selected:
                setattr(result, attribute, State.SKIPPED)
                continue
            setattr(result, attribute, State.RUNNING)
            if attribute == "git":
                runner.run([prerequisites.git, "init"], cwd=result.plan.path, timeout=30)
            elif attribute == "migrations":
                setattr(result, attribute, _migrate(result, runner, ui, no_interaction))
                continue
            else:
                launcher = resolve_executable(
                    "code", result.plan.path, excluded_roots=(result.plan.path,)
                )
                if launcher is None:
                    raise CompatibilityError(MESSAGES["editor_missing"])
                runner.open_editor(launcher, result.plan.path, cwd=result.plan.path)
            setattr(result, attribute, State.COMPLETED)
        except KeyboardInterrupt, Cancelled:
            setattr(result, attribute, State.CANCELLED)
            raise Cancelled(MESSAGES["post_cancelled"].format(path=result.plan.path)) from None
        except (InstallerError, OSError) as exc:
            setattr(result, attribute, State.FAILED)
            message = str(exc) if isinstance(exc, InstallerError) else MESSAGES["post_files_failed"]
            if attribute == "migrations" and not isinstance(exc, CompatibilityError):
                message += MESSAGES["migration_partial_warning"]
            result.warnings.append(message)
            ui.warning(message)
