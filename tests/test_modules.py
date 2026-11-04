"""Exercise installer modules with isolated files, processes, and terminal input."""

import ast
import errno
import importlib
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from typer.testing import CliRunner
from orionis_installer import __version__
from orionis_installer.cli import app
from orionis_installer.exceptions import CompatibilityError, ProcessError, ValidationError
from orionis_installer.installer import CONFIG_PROBE, METADATA_PROBE
from orionis_installer.models import (
    DEFAULT_STACK,
    STACKS,
    Database,
    InstallationPlan,
    InstallationResult,
    PostInstallOptions,
    Stack,
    State,
    Storage,
)
from orionis_installer.post_install import (
    connection_ready,
    connection_values,
    run_post_install,
    seeder_safety,
    valid_port,
)
from orionis_installer.prerequisites import Prerequisites, verify_python
from orionis_installer.processes import Runner, isolated_environment, resolve_executable
from orionis_installer.skeleton import publish, staging_destination
from orionis_installer.ui import UI, terminal_choice
from orionis_installer.ui.messages import STACK_CHOICES
from orionis_installer.ui.output import (
    _LOGO_PIXELS,
    _brand_logo,
    quote_directory,
    state_text,
)
from orionis_installer.ui.prompts import InputValidator, Prompts, selector_text
from orionis_installer.ui.theme import terminal_text
from orionis_installer.validation import validate_email, validate_name

SOURCE = Path(__file__).resolve().parents[1] / "src" / "orionis_installer"
MODULES = sorted(SOURCE.rglob("*.py"))


@pytest.mark.parametrize("path", MODULES, ids=lambda path: str(path.relative_to(SOURCE)))
def test_module_contract(path: Path) -> None:
    """Import every module and enforce documented method naming contracts.

    Parameters
    ----------
    path : Path
        Source module included in the exhaustive package inventory.
    """
    parts = path.relative_to(SOURCE).with_suffix("").parts
    name = ".".join(("orionis_installer", *parts))
    importlib.import_module(name)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert ast.get_docstring(node), f"{path}:{node.lineno} missing documentation"
        if isinstance(node, ast.ClassDef):
            for method in node.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if method.name.startswith("__") and method.name.endswith("__"):
                    continue
                if any(
                    isinstance(item, ast.Name) and item.id == "property"
                    for item in method.decorator_list
                ):
                    continue
                assert not re.search(r"[a-z]_[a-z]", method.name), method.name


def test_runner_success_failure_and_timeout(tmp_path: Path) -> None:
    """Execute real owned processes and enforce their exit and timeout contracts.

    Parameters
    ----------
    tmp_path : Path
        Neutral directory outside the repository.
    """
    runner = Runner(environ={})
    python = Path(sys.executable)
    result = runner.run([python, "-I", "-c", "print('ready')"], cwd=tmp_path)
    assert result.stdout.strip() == "ready"
    with pytest.raises(ProcessError):
        runner.run([python, "-I", "-c", "raise SystemExit(7)"], cwd=tmp_path)
    with pytest.raises(ProcessError):
        runner.run([python, "-I", "-c", "while True: pass"], cwd=tmp_path, timeout=0.1)


def test_environment_excludes_application_secrets(tmp_path: Path) -> None:
    """Exclude application settings and untrusted paths from tool environments.

    Parameters
    ----------
    tmp_path : Path
        Directory excluded from executable lookup.
    """
    environment = isolated_environment(
        {"APP_KEY": "private", "DB_PASSWORD": "private", "PATH": str(tmp_path)},
        cwd=tmp_path,
    )
    assert environment.keys().isdisjoint({"APP_KEY", "DB_PASSWORD"})
    assert environment["PATH"] == ""
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert resolve_executable("unavailable", tmp_path, path=str(tmp_path)) is None


def test_staging_reserves_and_cleans_owned_directory(tmp_path: Path) -> None:
    """Clean owned staging while preserving unrelated destination files.

    Parameters
    ----------
    tmp_path : Path
        Parent directory containing the reserved application destination.
    """
    destination = tmp_path / "example"
    with staging_destination(destination) as staging:
        assert staging.is_dir()
        with pytest.raises(ValidationError), staging_destination(destination):
            pytest.fail("A second reservation must fail")
        (staging / "owned.txt").write_text("owned", encoding="utf-8")
    assert not staging.exists()
    assert not destination.exists()
    assert not list(tmp_path.iterdir())


@pytest.fixture
def publication_paths(tmp_path: Path) -> tuple[Path, Path, dict[Path, bytes]]:
    """Prepare owned staging files and an unused sibling publication destination.

    Parameters
    ----------
    tmp_path : Path
        Disposable parent directory for the configured skeleton.

    Returns
    -------
    tuple[Path, Path, dict[Path, bytes]]
        Staging, destination and expected relative file contents.
    """
    staging = tmp_path / "staging"
    destination = tmp_path / "application"
    files = {
        Path("pyproject.toml"): b'[project]\nname="example"\n',
        Path("reactor"): b"runner\n",
        Path(".gitignore"): b".env\n.venv/\n",
        Path(".env"): b"APP_NAME=example\n",
        Path("bootstrap") / "app.py": b"bootstrap\n",
        Path("config") / "settings.py": b"settings\n",
    }
    for relative, contents in files.items():
        path = staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    return staging, destination, files


@pytest.mark.parametrize("name", ["config", "reactor"])
@pytest.mark.parametrize("winerror", [5, 32, 33])
@pytest.mark.parametrize("failures", [1, 4])
def test_publication_retries_transient_windows_locks(
    publication_paths: tuple[Path, Path, dict[Path, bytes]],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    winerror: int,
    failures: int,
) -> None:
    """Retry locked files and directories through the final permitted attempt.

    Parameters
    ----------
    publication_paths : tuple[Path, Path, dict[Path, bytes]]
        Owned staging, unused destination and expected file contents.
    monkeypatch : pytest.MonkeyPatch
        Scoped rename and retry-delay controls.
    name : str
        Staging entry temporarily blocked during publication.
    winerror : int
        Native Windows access, sharing or lock violation.
    failures : int
        Number of failed attempts before the lock is released.
    """
    staging, destination, files = publication_paths
    rename = Path.rename
    attempts = 0
    delays: list[float] = []
    created = Mock()

    def rename_with_lock(source: Path, target: Path) -> Path:
        """Block only the selected entry without changing either path."""
        nonlocal attempts
        if source == staging / name:
            attempts += 1
            if attempts <= failures:
                error = PermissionError(errno.EACCES, "Publication temporarily locked")
                error.winerror = winerror
                raise error
        return rename(source, target)

    monkeypatch.setattr(Path, "rename", rename_with_lock)
    monkeypatch.setattr(time, "sleep", delays.append)

    publish(staging, destination, on_created=created)

    created.assert_called_once_with()
    assert attempts == failures + 1
    assert delays == [0.1 * 2**attempt for attempt in range(failures)]
    assert not list(staging.iterdir())
    assert {
        path.relative_to(destination): path.read_bytes()
        for path in destination.rglob("*")
        if path.is_file()
    } == files


@pytest.mark.parametrize(
    ("error_number", "winerror", "expected_attempts"),
    [
        (errno.EACCES, 5, 5),
        (errno.EACCES, 32, 5),
        (errno.EACCES, 33, 5),
        (errno.EACCES, None, 1),
        (errno.EACCES, 1314, 1),
        (errno.ENOSPC, None, 1),
    ],
)
def test_publication_preserves_partial_destination_on_error(
    publication_paths: tuple[Path, Path, dict[Path, bytes]],
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
    winerror: int | None,
    expected_attempts: int,
) -> None:
    """Bound lock retries and retain published files when the same error persists.

    Parameters
    ----------
    publication_paths : tuple[Path, Path, dict[Path, bytes]]
        Owned staging, unused destination and expected file contents.
    monkeypatch : pytest.MonkeyPatch
        Scoped rename and retry-delay controls.
    error_number : int
        Error raised after publishing the first staging entry.
    winerror : int or None
        Optional native Windows error code.
    expected_attempts : int
        Maximum rename attempts for the failed entry.
    """
    staging, destination, files = publication_paths
    rename = Path.rename
    error = OSError(error_number, "Publication denied")
    if winerror is not None:
        error.winerror = winerror
    moved: list[Path] = []
    attempts = 0
    delays: list[float] = []
    created = Mock()

    def deny_after_first_entry(source: Path, target: Path) -> Path:
        """Publish one entry before denying every subsequent rename attempt."""
        nonlocal attempts
        if not moved:
            moved.append(rename(source, target))
            return moved[0]
        attempts += 1
        raise error

    monkeypatch.setattr(Path, "rename", deny_after_first_entry)
    monkeypatch.setattr(time, "sleep", delays.append)

    with pytest.raises(OSError) as caught:
        publish(staging, destination, on_created=created)

    assert caught.value is error
    created.assert_called_once_with()
    assert attempts == expected_attempts
    assert delays == [0.1 * 2**attempt for attempt in range(expected_attempts - 1)]
    published = {
        path.relative_to(destination): path.read_bytes()
        for path in destination.rglob("*")
        if path.is_file()
    }
    remaining = {
        path.relative_to(staging): path.read_bytes()
        for path in staging.rglob("*")
        if path.is_file()
    }
    assert published
    assert published.keys().isdisjoint(remaining)
    assert published | remaining == files


@pytest.mark.parametrize("changed", ["destination", "ancestor", "child", "identity"])
def test_publication_rechecks_controlled_paths_after_retry(
    publication_paths: tuple[Path, Path, dict[Path, bytes]],
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    """Refuse replaced or redirected paths before attempting a locked rename again.

    Parameters
    ----------
    publication_paths : tuple[Path, Path, dict[Path, bytes]]
        Owned staging, unused destination and expected file contents.
    monkeypatch : pytest.MonkeyPatch
        Scoped lock, redirect and retry-delay controls.
    changed : str
        Controlled path made unsafe while waiting to retry publication.
    """
    from orionis_installer import skeleton

    staging, destination, files = publication_paths
    rename, is_redirect = Path.rename, skeleton.is_redirect
    error = PermissionError(errno.EACCES, "Publication temporarily locked")
    error.winerror = 5
    attempts = 0
    child: Path | None = None
    armed = False
    delays: list[float] = []

    def deny_rename(source: Path, target: Path) -> Path:
        """Keep the staging entry intact while recording the first denied move."""
        nonlocal attempts, child
        attempts += 1
        child = source
        raise error

    def redirected_after_wait(path: Path) -> bool:
        """Expose the selected redirection only after the first failed rename."""
        unsafe = {"destination": destination, "ancestor": destination.parent, "child": child}
        return (armed and path == unsafe.get(changed)) or is_redirect(path)

    def change_path_during_wait(delay: float) -> None:
        """Replace destination ownership or expose a redirect during backoff."""
        nonlocal armed
        delays.append(delay)
        armed = True
        if changed == "identity":
            rename(destination, destination.with_name("original-destination"))
            destination.mkdir()
            (destination / "foreign.txt").write_bytes(b"foreign")

    monkeypatch.setattr(Path, "rename", deny_rename)
    monkeypatch.setattr(skeleton, "is_redirect", redirected_after_wait)
    monkeypatch.setattr(time, "sleep", change_path_during_wait)

    expected_error = CompatibilityError if changed == "child" else ValidationError
    with pytest.raises(expected_error):
        publish(staging, destination)

    assert attempts == 1
    assert delays == [0.1]
    assert {
        path.relative_to(staging): path.read_bytes()
        for path in staging.rglob("*")
        if path.is_file()
    } == files
    if changed == "identity":
        assert (destination / "foreign.txt").read_bytes() == b"foreign"
    else:
        assert not list(destination.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="Windows open-file directory rename semantics")
def test_publication_recovers_after_real_windows_file_lock(
    publication_paths: tuple[Path, Path, dict[Path, bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recover from native WinError 5 after an open child file releases its handle.

    Parameters
    ----------
    publication_paths : tuple[Path, Path, dict[Path, bytes]]
        Owned staging, unused destination and expected file contents.
    monkeypatch : pytest.MonkeyPatch
        Deterministic release of the real file handle during retry backoff.
    """
    staging, destination, files = publication_paths
    delays: list[float] = []
    with (staging / "config" / "settings.py").open("rb") as locked:

        def release_lock(delay: float) -> None:
            """Release the native file lock before the next publication attempt."""
            delays.append(delay)
            locked.close()

        monkeypatch.setattr(time, "sleep", release_lock)
        publish(staging, destination)

    assert delays == [0.1]
    assert not list(staging.iterdir())
    assert {
        path.relative_to(destination): path.read_bytes()
        for path in destination.rglob("*")
        if path.is_file()
    } == files


def test_prompts_accept_keyboard_input() -> None:
    """Accept selector navigation and validated text through a controlled terminal."""
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        prompts = Prompts(no_color=True)
        pipe.send_text("\x1b[B\r")
        assert prompts.select("Disk", [("local", "Local"), ("s3", "S3")], "local") == "s3"
        pipe.send_text("example\r")
        assert prompts.text("Name", validator=validate_name) == "example"
        pipe.send_text("n\r")
        assert not prompts.confirm("Continue?", True)
    assert selector_text([("local", "Local")], 0, "local")
    assert InputValidator(validate_name).callback is validate_name


def test_output_and_ui_states(tmp_path: Path) -> None:
    """Render every state and narrow output without exposing terminal controls.

    Parameters
    ----------
    tmp_path : Path
        Project destination displayed in the summary.
    """
    stream = io.StringIO()
    ui = UI(file=stream, width=40, no_color=True)
    plan = InstallationPlan(name="example", path=tmp_path)
    result = InstallationResult(plan, creation=State.COMPLETED, published=True)
    ui.banner()
    assert "Installer" in stream.getvalue()
    ui.summary(plan)
    assert plan.source.branch in stream.getvalue()
    with ui.progress() as progress:
        for value in ("source", "config", "sync", "verify"):
            progress.step(value)
        assert progress.__rich__()
    ui.final(result)
    ui.error("problem")
    assert stream.getvalue().count("example") > 0
    for state in State:
        assert state_text(state).plain.endswith(state.value)
    assert terminal_text("\x1b[31mred\x1b[0m") == "red"
    assert quote_directory(tmp_path)
    assert terminal_choice("Disk", [("local", "Local")], "local") == "Disk Local"
    with patch("orionis_installer.ui.has_tty", return_value=False), pytest.raises(ValidationError):
        ui.requireTty()


@pytest.mark.parametrize(
    "width, encoding",
    [
        (104, "utf-8"),
        (70, "utf-8"),
        (40, "utf-8"),
        (24, "utf-8"),
        (104, "ascii"),
        (40, "ascii"),
    ],
)
def test_banner_versions_and_logo(width: int, encoding: str) -> None:
    """Render local runtime information without network access or terminal overflow.

    Parameters
    ----------
    width : int
        Available terminal width in characters.
    encoding : str
        Output encoding selecting Unicode or ASCII rendering.
    """
    buffer = io.BytesIO()
    with (
        io.TextIOWrapper(buffer, encoding=encoding) as stream,
        patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("Banner must remain offline"),
        ) as lookup,
    ):
        ui = UI(file=stream, width=width, no_color=True)
        ui.banner()
        rendered = buffer.getvalue().decode(encoding)

    assert "Installer" in rendered
    assert "v" + __version__ in rendered
    assert "Python 3.14" in rendered
    assert all(len(line) <= width for line in rendered.splitlines())
    assert "\x1b" not in rendered
    if width >= 70 and encoding == "utf-8":
        assert any(0x2801 <= ord(character) <= 0x28FF for character in rendered)
    else:
        assert not any(0x2801 <= ord(character) <= 0x28FF for character in rendered)
    lookup.assert_not_called()


def test_logo_bitmap_and_palette() -> None:
    """Preserve every silhouette pixel and the official three-color palette."""
    assert len(_LOGO_PIXELS) == 52
    assert {len(row) for row in _LOGO_PIXELS} == {50}
    logo = _brand_logo()
    rows = logo.plain.splitlines()
    assert len(rows) == 13
    assert {len(row) for row in rows} == {25}
    assert {span.style for span in logo.spans} == {"logo_outline", "logo_star", "logo_spark"}
    dot_bits = ((0, 3), (1, 4), (2, 5), (6, 7))
    for row, line in enumerate(rows):
        for column, character in enumerate(line):
            dots = 0 if character == " " else ord(character) - 0x2800
            for row_offset, bits in enumerate(dot_bits):
                for column_offset, bit in enumerate(bits):
                    pixel = _LOGO_PIXELS[row * 4 + row_offset][column * 2 + column_offset]
                    assert bool(dots & (1 << bit)) == (pixel != ".")


def test_compact_logo_dimensions_and_palette() -> None:
    """Retain seven compact raster rows and the official three-color palette."""
    logo = _brand_logo(compact=True)
    rows = logo.plain.splitlines()
    assert len(rows) == 7
    assert {len(row) for row in rows} == {13}
    assert {span.style for span in logo.spans} == {"logo_outline", "logo_star", "logo_spark"}


def test_post_install_noninteractive_defaults(tmp_path: Path) -> None:
    """Skip unrequested follow-up work and never prompt for noninteractive defaults.

    Parameters
    ----------
    tmp_path : Path
        Published application directory.
    """
    result = InstallationResult(
        InstallationPlan(name="example", path=tmp_path), creation=State.COMPLETED
    )
    runner, ui = Mock(), Mock()
    prerequisites = Prerequisites(tmp_path / "uv", tmp_path / "git", Path(sys.executable), "3.14.0")
    run_post_install(result, PostInstallOptions(), prerequisites, runner, ui, no_interaction=True)
    assert result.git == State.SKIPPED
    assert result.migrations == State.SKIPPED
    ui.confirm.assert_not_called()
    runner.run.assert_not_called()
    assert result.exit_code == 0


def test_python_and_connection_checks(tmp_path: Path) -> None:
    """Verify interpreter and connection readiness without opening database sockets.

    Parameters
    ----------
    tmp_path : Path
        Isolated directory containing connection settings.
    """
    runner = Mock()
    runner.run.return_value = subprocess.CompletedProcess([], 0, '[3, 14, 0, "final"]')
    assert verify_python(runner, Path(sys.executable), cwd=tmp_path) == "3.14.0"
    (tmp_path / ".env").write_text(
        "DB_CONNECTION=sqlite\nDB_DATABASE=data.sqlite\n", encoding="utf-8"
    )
    assert connection_ready(connection_values(tmp_path))
    assert not valid_port("65536")
    assert valid_port("5432")
    seeder_safety(tmp_path)
    (tmp_path / "database" / "seeders").mkdir(parents=True)
    (tmp_path / "database" / "seeders" / "unsafe.py").write_text("run()", encoding="utf-8")
    with pytest.raises(CompatibilityError):
        seeder_safety(tmp_path)


def test_public_constants_and_validation() -> None:
    """Verify source catalogs and portable application metadata validation."""
    assert __version__
    expected = {
        "master": "master",
        "blank": "blank_1.x",
        "ssr": "ssr_1.x",
        "slim_api": "slim_api_1.x",
        "slim_mcp": "slim_mcp_1.x",
        "slim_worker": "slim_worker_1.x",
        "slim_realtime": "slim_realtime_1.x",
    }
    assert {stack.value: source.branch for stack, source in STACKS.items()} == expected
    assert set(STACKS) == set(Stack)
    assert [(stack.value, source.label) for stack, source in STACKS.items()] == STACK_CHOICES
    assert DEFAULT_STACK == Stack.BLANK
    assert Storage.ALL.value == "all"
    assert Database.ALL.value == "all"
    assert validate_email("name@example.com") == "name@example.com"
    for name in ("../example", "CON", "bad name"):
        with pytest.raises(ValidationError):
            validate_name(name)


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("stack", [None, *(stack.value for stack in Stack)])
def test_offline_cli_installation(tmp_path: Path, failure: bool, stack: str | None) -> None:
    """Exercise CLI publication and recovery using controlled tool responses.

    Parameters
    ----------
    tmp_path : Path
        Parent containing the application's exclusively owned destination.
    failure : bool
        Fail synchronization after publication to verify recovery behavior.
    stack : str or None
        Stack selected explicitly, or None to retain the default.
    """
    destination = tmp_path / "application"
    selected = Stack(stack) if stack is not None else DEFAULT_STACK
    source = STACKS[selected]
    runner = Mock()
    runner.environ = {}
    prerequisites = Prerequisites(
        tmp_path / "uv.exe",
        tmp_path / "git.exe",
        Path(sys.executable),
        "3.14.0",
    )

    def run_tool(
        argv: list[object],
        *,
        cwd: Path,
        **options: object,
    ) -> subprocess.CompletedProcess:
        """Supply tool results while creating only the owned fixture application.

        Parameters
        ----------
        argv : list[object]
            Controlled native tool arguments.
        cwd : Path
            Tool working directory selected by the installer.
        options : object
            Additional controlled runner options.

        Returns
        -------
        subprocess.CompletedProcess
            Tool output or verification metadata for the requested operation.

        Raises
        ------
        ProcessError
            If synchronization is deliberately failed after publication.
        """
        output = ""
        if argv[1] == "clone":
            assert argv[argv.index("--branch") + 1] == source.branch
            assert argv[-2] == source.repository
            staging = Path(argv[-1])
            for name in ("config", "bootstrap", "database", ".git"):
                (staging / name).mkdir()
            for name in ("reactor", "bootstrap/app.py", ".gitignore"):
                (staging / name).write_text("", encoding="utf-8")
            (staging / "pyproject.toml").write_text(
                '[project]\nname="template"\nrequires-python=">=3.14"\n'
                'dependencies=["orionis>=1.5.0,<2.0.0"]\n',
                encoding="utf-8",
            )
            (staging / ".env.example").write_text(
                "APP_NAME=Template\nAPP_KEY=\nDB_CONNECTION=sqlite\n"
                "DB_DATABASE=database/app.sqlite\nFILESYSTEM_DISK=local\n",
                encoding="utf-8",
            )
            (staging / ".env.reference").write_text(
                "# All available environment settings\nAPP_NAME=Reference\n",
                encoding="utf-8",
            )
            (staging / "config" / "database.py").write_text(
                'default=Env.get("DB_CONNECTION")\nconnections=Connections(sqlite=None)\n',
                encoding="utf-8",
            )
            (staging / "config" / "filesystems.py").write_text(
                'default=Env.get("FILESYSTEM_DISK")\ndisks=Disks(local=None)\n',
                encoding="utf-8",
            )
        elif argv[1] == "symbolic-ref":
            output = f"refs/heads/{source.branch}"
        elif argv[1] == "rev-parse":
            output = "a" * 40
        elif argv[1] == "sync":
            if failure:
                raise ProcessError("Controlled sync failure")
            scripts = cwd / ".venv" / ("Scripts" if os.name == "nt" else "bin")
            scripts.mkdir(parents=True)
            (scripts / ("python.exe" if os.name == "nt" else "python")).touch()
            (cwd / "uv.lock").touch()
        elif argv[-1] == METADATA_PROBE:
            output = json.dumps(
                {
                    "python": "3.14.0",
                    "version": "1.5.0",
                    "prefix": str(cwd / ".venv"),
                    "extras": [],
                    "requirements": [],
                    "installed": {},
                }
            )
        elif argv[-1] == CONFIG_PROBE:
            output = json.dumps(
                {
                    "database": "sqlite",
                    "driver": "sqlite",
                    "storage": "local",
                    "storage_driver": "local",
                    "name": "offline-example",
                }
            )
        elif argv[-1] == "key:generate":
            path = cwd / ".env"
            content = path.read_text(encoding="utf-8").replace("APP_KEY=", "APP_KEY=test-key")
            path.write_text(content, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout=output)

    runner.run.side_effect = run_tool
    with (
        patch("orionis_installer.cli.Runner", return_value=runner),
        patch(
            "orionis_installer.cli.check_prerequisites",
            return_value=prerequisites,
        ),
    ):
        response = CliRunner().invoke(
            app,
            [
                "new",
                "offline-example",
                "--path",
                str(destination),
                "--no-interaction",
                "--no-color",
                "--git",
                "--no-migrate",
                *(["--stack", stack.upper()] if stack is not None else []),
            ],
        )
    assert response.exit_code == (1 if failure else 0), response.output
    assert source.label in response.output
    assert source.branch in response.output
    assert (destination / ".env").is_file()
    assert not (destination / ".env.example").exists()
    assert (destination / ".env.reference").read_text(encoding="utf-8") == (
        "# All available environment settings\nAPP_NAME=Reference\n"
    )
    assert not list(tmp_path.glob(".*.orionis-*"))
    if failure:
        assert response.output.count("preserved") > 0
    else:
        assert response.output.count("Application ready") > 0
