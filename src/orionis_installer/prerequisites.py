"""Resolve uv, Git and a verified Python 3.14 before creating project files."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from orionis_installer.messages import MESSAGES

from .exceptions import PrerequisiteError, ProcessError
from .processes import Runner, isolated_environment, resolve_executable

PYTHON_REQUEST = "3.14"
_VERSION_PROBE = (
    "import json,sys; print(json.dumps([*sys.version_info[:3], sys.version_info.releaselevel]))"
)


@dataclass(frozen=True, slots=True)
class Prerequisites:
    """Store verified executables and the selected application Python version."""

    uv: Path
    git: Path
    python: Path
    python_version: str


def verify_python(runner: Runner, interpreter: Path, *, cwd: Path) -> str:
    """Verify a stable Python 3.14 interpreter through an isolated probe.

    Parameters
    ----------
    runner : Runner
        Process component used to execute the version probe.
    interpreter : Path
        Absolute interpreter path discovered by uv.
    cwd : Path
        Neutral working directory for the probe.

    Returns
    -------
    str
        Verified major, minor and patch version.

    Raises
    ------
    PrerequisiteError
        If probing fails or the interpreter is not stable Python 3.14.x.
    """
    try:
        result = runner.run([interpreter, "-I", "-B", "-c", _VERSION_PROBE], cwd=cwd, timeout=30)
        version = json.loads(result.stdout)
    except ProcessError, json.JSONDecodeError:
        raise PrerequisiteError(MESSAGES["python_probe_failed"]) from None
    if (
        not isinstance(version, list)
        or len(version) != 4
        or version[:2] != [3, 14]
        or not isinstance(version[2], int)
        or version[3] != "final"
    ):
        raise PrerequisiteError(MESSAGES["python_stable_required"])
    return ".".join(str(part) for part in version[:3])


def check_prerequisites(
    runner: Runner,
    *,
    cwd: Path,
    announce: Callable[[str], None] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Prerequisites:
    """Resolve prerequisites while preserving uv network and download policies.

    Discovery runs in a neutral temporary directory so a caller's .venv or
    workspace cannot supply the application interpreter. No venv is created here.
    The uv-managed interpreter is installed without bin links or registry edits.

    Parameters
    ----------
    runner : Runner
        Isolated process component for prerequisite checks.
    cwd : Path
        Caller directory excluded from executable discovery.
    announce : Callable[[str], None] or None, optional
        Callback notified before a managed Python installation is attempted.
    environ : Mapping[str, str] or None, optional
        Explicit environment snapshot; use the runner snapshot when omitted.

    Returns
    -------
    Prerequisites
        Trusted uv, Git and verified Python executable paths.

    Raises
    ------
    PrerequisiteError
        If prerequisites or supported download configuration are unavailable.
    Cancelled
        If the user interrupts a prerequisite process.
    """
    environment = runner.environ if environ is None else environ
    if any(key.upper() == "UV_CONFIG_FILE" and value for key, value in environment.items()):
        raise PrerequisiteError(MESSAGES["custom_uv_config_unsupported"])
    isolated = isolated_environment(environment, cwd=cwd)
    search_path = next((value for key, value in isolated.items() if key.upper() == "PATH"), "")
    uv = resolve_executable("uv", cwd, path=search_path)
    git = resolve_executable("git", cwd, path=search_path)
    if uv is None:
        raise PrerequisiteError(MESSAGES["uv_missing"])
    if git is None:
        raise PrerequisiteError(MESSAGES["git_missing"])
    if any(executable.suffix.lower() in {".cmd", ".bat"} for executable in (uv, git)):
        raise PrerequisiteError(MESSAGES["prerequisite_wrapper_unsupported"])
    try:
        runner.run([uv, "--version"], cwd=cwd, timeout=30)
        runner.run([git, "--version"], cwd=cwd, timeout=30)
    except ProcessError:
        raise PrerequisiteError(MESSAGES["prerequisite_execution_failed"]) from None
    with tempfile.TemporaryDirectory(prefix="orionis-python-check-") as temporary:
        neutral_directory = Path(temporary)
        find_command: list[str | Path] = [
            uv,
            "python",
            "find",
            PYTHON_REQUEST,
            "--no-project",
            "--no-python-downloads",
        ]
        found = runner.run(
            find_command, cwd=neutral_directory, env=isolated, timeout=60, check=False
        )
        if found.returncode:
            normalized = {key.upper(): value for key, value in environment.items()}
            if normalized.get("UV_PYTHON_DOWNLOADS", "").lower() == "never":
                raise PrerequisiteError(MESSAGES["python_downloads_disabled"])
            if normalized.get("UV_OFFLINE", "").lower() in {"1", "true", "yes"}:
                raise PrerequisiteError(MESSAGES["python_offline_missing"])
            if announce:
                announce(MESSAGES["python_download_announcement"])
            try:
                runner.run(
                    [uv, "python", "install", PYTHON_REQUEST, "--no-bin", "--no-registry"],
                    cwd=neutral_directory,
                    env=isolated,
                    timeout=600,
                )
            except ProcessError:
                raise PrerequisiteError(MESSAGES["python_download_failed"]) from None
            found = runner.run(
                find_command, cwd=neutral_directory, env=isolated, timeout=60, check=False
            )
        if found.returncode:
            raise PrerequisiteError(MESSAGES["python_find_failed"])
        interpreter = Path(found.stdout.strip())
        if not interpreter.is_absolute() or not interpreter.is_file():
            raise PrerequisiteError(MESSAGES["python_find_invalid"])
        interpreter = interpreter.resolve()
        original = cwd.resolve()
        if interpreter.parent == original or original / ".venv" in interpreter.parents:
            raise PrerequisiteError(MESSAGES["python_working_directory_invalid"])
        version = verify_python(runner, interpreter, cwd=neutral_directory)
    return Prerequisites(uv, git, interpreter, version)
