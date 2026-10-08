import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from orionis_installer.configuration import (
    configure_environment,
    configure_pyproject,
    ensure_gitignore,
    read_env,
)
from orionis_installer.exceptions import Cancelled, CompatibilityError, InstallerError
from orionis_installer.messages import MESSAGES
from orionis_installer.models import Database, InstallationPlan, InstallationResult, State, Storage
from orionis_installer.prerequisites import Prerequisites
from orionis_installer.processes import Runner
from orionis_installer.skeleton import clone_skeleton, publish, staging_destination
from orionis_installer.validation import is_redirect

# Run in the application's Python, never import the framework in this process.
METADATA_PROBE = """import json,sys,importlib.metadata as m
d=m.distribution('orionis')
print(json.dumps({'python':'.'.join(map(str,sys.version_info[:3])), 'prefix':sys.prefix,
 'version':d.version,'extras':d.metadata.get_all('Provides-Extra') or [],
 'requirements':d.requires or [],
 'installed':{x.metadata['Name'].lower().replace('_','-'):x.version for x in m.distributions()}}))
"""
CONFIG_PROBE = """import json
from config.database import BootstrapDatabase
from config.filesystems import BootstrapFilesystems
from orionis.environment import Env
d=BootstrapDatabase();s=BootstrapFilesystems()
db=str(d.default);disk=str(s.default)
c=getattr(d.connections,db);f=getattr(s.disks,disk)
print(json.dumps({'database':db,'driver':str(c.driver),'storage':disk,
 'storage_driver':str(f.driver),'name':Env.get('APP_NAME')}))
"""

def project_python(root: Path) -> Path:
    """
    Locate the interpreter inside the final application's direct environment.

    Parameters
    ----------
    root : Path
        Published application directory.

    Returns
    -------
    Path
        Platform-specific Python executable inside the local .venv.

    Raises
    ------
    CompatibilityError
        If the environment is redirected or its interpreter is absent.
    """
    environment = root / ".venv"
    if not environment.is_dir() or is_redirect(environment):
        raise CompatibilityError(MESSAGES["project_environment_missing"])
    interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not interpreter.is_file():
        raise CompatibilityError(MESSAGES["project_interpreter_missing"])
    return interpreter

def probe_json(runner: Runner, python: Path, root: Path, source: str) -> dict[str, Any]:
    """
    Parse a JSON verification probe executed with the application's Python.

    Parameters
    ----------
    runner : Runner
        Isolated process component.
    python : Path
        Verified project interpreter.
    root : Path
        Published application root used as the working directory.
    source : str
        Controlled Python probe source.

    Returns
    -------
    dict[str, Any]
        JSON metadata emitted on the probe's final output line.

    Raises
    ------
    CompatibilityError
        If the probe does not emit valid JSON metadata.
    ProcessError
        If the probe process fails.
    """
    completed = runner.run([python, "-B", "-c", source], cwd=root, timeout=60)
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise CompatibilityError(MESSAGES["project_probe_invalid"]) from exc

def _requirements_for(data: dict[str, Any], extra: str) -> set[str]:
    """
    Collect normalized dependencies selected by one framework extra.

    Parameters
    ----------
    data : dict[str, Any]
        Framework metadata including Python version and declared requirements.
    extra : str
        Extra whose dependency markers will be evaluated.

    Returns
    -------
    set[str]
        Normalized names of marker-selected dependencies.
    """
    environment: dict[str, str] = {key: str(value) for key, value in default_environment().items()}
    environment["python_version"] = "3.14"
    environment["python_full_version"] = data["python"]
    environment["extra"] = extra
    return {
        canonicalize_name(r.name)
        for raw in data["requirements"]
        if (r := Requirement(raw)).marker and r.marker.evaluate(environment)
    }

def verify_metadata( # NOSONAR
    data: dict[str, Any],
    plan: InstallationPlan,
    requirement: Requirement,
) -> None:
    """
    Verify resolved framework extras, dependencies and environment identity.

    Parameters
    ----------
    data : dict[str, Any]
        Metadata collected from the installed project environment.
    plan : InstallationPlan
        Validated application choices and final destination.
    requirement : Requirement
        Skeleton requirement with all requested extras merged.

    Raises
    ------
    CompatibilityError
        If Python, environment identity, extras or effective dependencies differ.
    """
    if not data["python"].startswith("3.14."):
        raise CompatibilityError(MESSAGES["project_python_invalid"])
    expected_prefix = (plan.path / ".venv").resolve()
    if Path(data["prefix"]).resolve() != expected_prefix:
        raise CompatibilityError(MESSAGES["project_environment_redirected"])
    if not requirement.specifier.contains(data["version"], prereleases=None):
        raise CompatibilityError(MESSAGES["framework_requirement_mismatch"])
    available = {canonicalize_name(extra) for extra in data["extras"]}
    missing = {canonicalize_name(extra) for extra in requirement.extras} - available
    if missing:
        raise CompatibilityError(
            MESSAGES["framework_extras_missing_prefix"] + ", ".join(sorted(missing))
        )
    for aggregate, offered in (
        ("database", {d.value for d in Database if d not in (Database.SQLITE, Database.ALL)}),
        ("storage", {s.value for s in Storage if s not in (Storage.LOCAL, Storage.ALL)}),
    ):
        if aggregate in requirement.extras:
            if not offered <= available:
                raise CompatibilityError(
                    MESSAGES["aggregate_drivers_missing"].format(aggregate=aggregate)
                )
            aggregate_dependencies = _requirements_for(data, aggregate)
            if any(
                not _requirements_for(data, driver) <= aggregate_dependencies for driver in offered
            ):
                raise CompatibilityError(
                    MESSAGES["aggregate_drivers_incomplete"].format(aggregate=aggregate)
                )
    installed = {canonicalize_name(k): v for k, v in data["installed"].items()}
    for extra in ("", *requirement.extras):
        environment = {key: str(value) for key, value in default_environment().items()}
        environment.update(python_version="3.14", python_full_version=data["python"], extra=extra)
        for raw in data["requirements"]:
            dependency = Requirement(raw)
            if dependency.marker and not dependency.marker.evaluate(environment):
                continue
            name = canonicalize_name(dependency.name)
            if name not in installed or not dependency.specifier.contains(installed[name]):
                raise CompatibilityError(MESSAGES["framework_dependency_missing"])

def verify_configuration(data: dict[str, Any], plan: InstallationPlan) -> None:
    """
    Check that effective framework configuration matches the installation plan.

    Parameters
    ----------
    data : dict[str, Any]
        Active database, storage and application values from the project probe.
    plan : InstallationPlan
        Selected application name and active drivers.

    Raises
    ------
    CompatibilityError
        If any effective value differs from the selected plan.
    """
    if (
        data.get("database") != plan.active_database.value
        or data.get("driver") != plan.active_database.value
        or data.get("storage") != plan.active_storage.value
        or data.get("storage_driver") != plan.active_storage.value
        or data.get("name") != plan.name
    ):
        raise CompatibilityError(MESSAGES["effective_configuration_mismatch"])

def ensure_app_key(runner: Runner, python: Path, root: Path) -> None:
    """
    Generate a missing application key through the verified framework mechanism.

    Parameters
    ----------
    runner : Runner
        Isolated process component for bootstrap and Reactor execution.
    python : Path
        Verified project interpreter.
    root : Path
        Application root containing its configured dotenv file.

    Raises
    ------
    CompatibilityError
        If key generation fails or bootstrap modifies an existing key.
    ProcessError
        If bootstrap or the key command exits unsuccessfully.
    """
    path = root / ".env"
    before = read_env(path).get("APP_KEY")
    # Orionis casts these unprefixed dotenv sentinels to None. Prefixes such as
    # str:null must retain their meaning; invalid existing keys are never replaced.
    missing = before is None or before.strip().lower() in {"", "none", "null", "nil", "nan"}
    # Bootstrap creates the key if missing; the command's default preserves existing keys.
    if missing:
        runner.run([python, "-B", "reactor", "key:generate"], cwd=root, timeout=90)
    else:
        runner.run([python, "-B", "-c", "from bootstrap.app import app"], cwd=root, timeout=90)
    after = read_env(path).get("APP_KEY")
    if after is None or after.strip().lower() in {"", "none", "null", "nil", "nan"}:
        raise CompatibilityError(MESSAGES["app_key_missing"])
    if not missing and before != after:
        raise CompatibilityError(MESSAGES["app_key_changed"])

class Installer:
    """Coordinate verified creation while preserving published application files."""

    def __init__(
        self,
        plan: InstallationPlan,
        prerequisites: Prerequisites,
        runner: Runner,
        *,
        on_step: Callable[[str], None] | None = None,
    ) -> None:
        """
        Initialize the coordinator with validated choices and process dependencies.

        Parameters
        ----------
        plan : InstallationPlan
            Validated application choices and final destination.
        prerequisites : Prerequisites
            Verified uv, Git and target Python prerequisites.
        runner : Runner
            Isolated process component used for every external operation.
        on_step : Callable[[str], None] or None, optional
            Callback receiving sanitized installation phase messages.
        """
        self.plan = plan
        self.prerequisites = prerequisites
        self.runner = runner
        self.on_step = on_step

    def _step(self, message: str) -> None:
        """
        Notify the optional phase callback before a controlled operation.

        Parameters
        ----------
        message : str
            Sanitized phase message for the installer interface.
        """
        if self.on_step:
            self.on_step(message)

    def install(self) -> InstallationResult: # NOSONAR
        """
        Create and verify an application before reporting completion.

        Returns
        -------
        InstallationResult
            Verified creation state, installed versions and pending setup warnings.

        Raises
        ------
        InstallerError
            If cloning, configuration, publication or synchronization fails.
        CompatibilityError
            If the resolved application violates the inspected contract.
        Cancelled
            If the user cancels; published application files remain preserved.
        """
        result = InstallationResult(self.plan, creation=State.RUNNING)
        try:
            with staging_destination(self.plan.path) as staging:
                self._step(
                    MESSAGES["phase_clone"].format(
                        stack=self.plan.source.label,
                        repository=self.plan.source.repository,
                        branch=self.plan.source.branch,
                    )
                )
                clone_skeleton(
                    staging, self.prerequisites.git, self.runner, source=self.plan.source
                )
                self._step(MESSAGES["phase_configuration"])
                requirement = configure_pyproject(
                    staging, self.plan, python_version=self.prerequisites.python_version
                )
                configure_environment(staging, self.plan)
                ensure_gitignore(staging)
                publish(
                    staging, self.plan.path, on_created=lambda: setattr(result, "published", True)
                )
            self._step(MESSAGES["phase_sync"])
            self.runner.run(
                [self.prerequisites.uv, "sync", "--python", "3.14"],
                cwd=self.plan.path,
                timeout=900,
            )
            python = project_python(self.plan.path)
            self._step(MESSAGES["phase_verification"])
            data = probe_json(self.runner, python, self.plan.path, METADATA_PROBE)
            verify_metadata(data, self.plan, requirement)
            if not (self.plan.path / "uv.lock").is_file():
                raise CompatibilityError(MESSAGES["project_lock_missing"])
            verify_configuration(
                probe_json(self.runner, python, self.plan.path, CONFIG_PROBE), self.plan
            )
            ensure_app_key(self.runner, python, self.plan.path)
            result.python_version, result.framework_version = data["python"], data["version"]
            if self.plan.active_storage != Storage.LOCAL:
                result.warnings.append(MESSAGES["cloud_configuration_pending"])
            if self.plan.active_database != Database.SQLITE:
                database_key = (
                    MESSAGES["oracle_connection_keys"]
                    if self.plan.active_database == Database.ORACLE
                    else "DB_DATABASE"
                )
                result.warnings.append(
                    MESSAGES["database_configuration_pending"].format(database_key=database_key)
                )
            if self.plan.active_database == Database.SQLSERVER:
                result.warnings.append(MESSAGES["sqlserver_system_driver_required"])
            result.creation = State.COMPLETED
            return result
        except KeyboardInterrupt, Cancelled:
            result.creation = State.CANCELLED
            raise Cancelled(self._recovery(result, MESSAGES["installation_cancelled"])) from None
        except InstallerError as exc:
            result.creation = State.FAILED
            diagnostic = str(exc)
            if isinstance(exc, CompatibilityError):
                diagnostic = MESSAGES["skeleton_source_context"].format(
                    diagnostic=diagnostic,
                    repository=self.plan.source.repository,
                    branch=self.plan.source.branch,
                )
            if result.published:
                if isinstance(exc, CompatibilityError):
                    raise CompatibilityError(self._recovery(result, diagnostic)) from exc
                raise InstallerError(self._recovery(result, diagnostic)) from exc
            if isinstance(exc, CompatibilityError):
                raise CompatibilityError(diagnostic) from exc
            raise
        except OSError as exc:
            result.creation = State.FAILED
            raise InstallerError(
                self._recovery(result, MESSAGES["installation_files_failed"])
            ) from exc

    def _recovery(self, result: InstallationResult, message: str) -> str:
        """
        Append recovery instructions only after final-directory publication.

        Parameters
        ----------
        result : InstallationResult
            Observed publication and creation state.
        message : str
            Sanitized failure or cancellation diagnostic.

        Returns
        -------
        str
            Diagnostic including recovery commands when files were published.
        """
        if result.published:
            return message + MESSAGES["installation_recovery"].format(path=self.plan.path)
        return message
