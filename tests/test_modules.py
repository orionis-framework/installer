"""Exercise installer modules with isolated files, processes, and terminal input."""

import ast
import importlib
import io
import json
import os
import re
import subprocess
import sys
from http.client import HTTPException
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

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
    STACKS, Database, InstallationPlan, InstallationResult, PostInstallOptions, State, Storage,
)
from orionis_installer.post_install import (
    connection_ready, connection_values, run_post_install, seeder_safety, valid_port,
)
from orionis_installer.prerequisites import Prerequisites, verify_python
from orionis_installer.processes import Runner, isolated_environment, resolve_executable
from orionis_installer.skeleton import staging_destination
from orionis_installer.ui import UI, terminal_choice
from orionis_installer.ui.output import (
    _LOGO_PIXELS, _brand_logo, framework_version, quote_directory, state_text,
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
                if any(isinstance(item, ast.Name) and item.id == "property"
                       for item in method.decorator_list):
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
    with patch("orionis_installer.ui.output.framework_version", return_value=None):
        ui.banner()
    assert "unavailable" in stream.getvalue()
    ui.summary(plan)
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


@pytest.mark.parametrize("width, encoding", [
    (104, "utf-8"), (70, "utf-8"), (40, "utf-8"), (24, "utf-8"),
    (104, "ascii"), (40, "ascii"),
])
def test_banner_versions_and_logo(width: int, encoding: str) -> None:
    """Display both versions and the brand logo within terminal constraints.

    Parameters
    ----------
    width : int
        Available terminal width in characters.
    encoding : str
        Output encoding selecting Unicode or ASCII rendering.
    """
    buffer = io.BytesIO()
    payload = json.dumps({"info": {"version": "9.87.6"}}).encode()
    with io.TextIOWrapper(buffer, encoding=encoding) as stream, patch(
        "urllib.request.urlopen", return_value=io.BytesIO(payload),
    ) as lookup:
        ui = UI(file=stream, width=width, no_color=True)
        ui.banner()
        rendered = buffer.getvalue().decode(encoding)

    assert "Framework" in rendered
    assert "v9.87.6" in rendered
    assert "Installer" in rendered
    assert "v" + __version__ in rendered
    assert "Python 3.14" in rendered
    assert all(len(line) <= width for line in rendered.splitlines())
    assert "\x1b" not in rendered
    if width >= 70 and encoding == "utf-8":
        assert any(0x2801 <= ord(character) <= 0x28FF for character in rendered)
        assert "\u2726" not in rendered
    else:
        assert not any(0x2801 <= ord(character) <= 0x28FF for character in rendered)
    lookup.assert_called_once_with("https://pypi.org/pypi/orionis/json", timeout=2)


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


@pytest.mark.parametrize("payload", [
    b"not-json", b"{}", b"[]", b'{"info": {}}', b'{"info": []}',
    b'{"info": {"version": null}}', b'{"info": {"version": 805}}',
    b'{"info": {"version": "not-a-version"}}',
    b'{"info": {"version": "\\u001b[31m0.805.0"}}',
])
def test_framework_version_rejects_invalid_metadata(payload: bytes) -> None:
    """Reject malformed PyPI metadata and terminal control sequences.

    Parameters
    ----------
    payload : bytes
        Invalid JSON response or unusable package version metadata.
    """
    with patch("urllib.request.urlopen", return_value=io.BytesIO(payload)):
        assert framework_version() is None


@pytest.mark.parametrize("failure", [
    TimeoutError("timed out"), URLError("offline"), HTTPException("invalid response"),
    HTTPError("https://pypi.org/pypi/orionis/json", 503, "unavailable", None, None),
])
def test_banner_tolerates_unavailable_pypi(failure: Exception) -> None:
    """Keep the installer banner usable when the PyPI lookup fails.

    Parameters
    ----------
    failure : Exception
        Transport or HTTP failure raised by the controlled PyPI request.
    """
    stream = io.StringIO()
    with patch("urllib.request.urlopen", side_effect=failure):
        UI(file=stream, width=104, no_color=True).banner()
    rendered = stream.getvalue()
    assert "Framework unavailable" in rendered
    assert "Installer v" + __version__ in rendered


def test_post_install_noninteractive_defaults(tmp_path: Path) -> None:
    """Skip unrequested follow-up work and never prompt for noninteractive defaults.

    Parameters
    ----------
    tmp_path : Path
        Published application directory.
    """
    result = InstallationResult(InstallationPlan(name="example", path=tmp_path),
                                creation=State.COMPLETED)
    runner, ui = Mock(), Mock()
    prerequisites = Prerequisites(tmp_path / "uv", tmp_path / "git", Path(sys.executable), "3.14.0")
    run_post_install(result, PostInstallOptions(), prerequisites, runner, ui, no_interaction=True)
    assert result.git == State.SKIPPED
    assert result.migrations == State.SKIPPED
    assert result.editor == State.SKIPPED
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
    runner.run.return_value = subprocess.CompletedProcess([], 0, "[3, 14, 0, \"final\"]")
    assert verify_python(runner, Path(sys.executable), cwd=tmp_path) == "3.14.0"
    (tmp_path / ".env").write_text("DB_CONNECTION=sqlite\nDB_DATABASE=data.sqlite\n",
                                   encoding="utf-8")
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
    assert len(STACKS) == 2
    assert Storage.ALL.value == "all"
    assert Database.ALL.value == "all"
    assert validate_email("name@example.com") == "name@example.com"
    for name in ("../example", "CON", "bad name"):
        with pytest.raises(ValidationError):
            validate_name(name)


@pytest.mark.parametrize("failure", [False, True])
def test_offline_cli_installation(tmp_path: Path, failure: bool) -> None:
    """Exercise CLI publication and recovery using controlled tool responses.

    Parameters
    ----------
    tmp_path : Path
        Parent containing the application's exclusively owned destination.
    failure : bool
        Fail synchronization after publication to verify recovery behavior.
    """
    destination = tmp_path / "application"
    runner = Mock()
    runner.environ = {}
    prerequisites = Prerequisites(
        tmp_path / "uv.exe", tmp_path / "git.exe", Path(sys.executable), "3.14.0",
    )

    def run_tool(
        argv: list[object], *, cwd: Path, **options: object,
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
            staging = Path(argv[-1])
            for name in ("config", "bootstrap", "database", ".git"):
                (staging / name).mkdir()
            for name in ("reactor", "bootstrap/app.py", ".gitignore"):
                (staging / name).write_text("", encoding="utf-8")
            (staging / "pyproject.toml").write_text(
                '[project]\nname="template"\nrequires-python=">=3.14"\n'
                'dependencies=["orionis>=0.805.0"]\n', encoding="utf-8",
            )
            (staging / ".env.example").write_text(
                "APP_NAME=Template\nAPP_KEY=\nDB_CONNECTION=sqlite\n"
                "DB_DATABASE=database/app.sqlite\nFILESYSTEM_DISK=local\n", encoding="utf-8",
            )
            (staging / "config" / "database.py").write_text(
                'default=Env.get("DB_CONNECTION")\nconnections=Connections(sqlite=None)\n',
                encoding="utf-8",
            )
            (staging / "config" / "filesystems.py").write_text(
                'default=Env.get("FILESYSTEM_DISK")\ndisks=Disks(local=None)\n', encoding="utf-8",
            )
        elif argv[1] == "symbolic-ref":
            output = "refs/heads/blank_1.x"
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
            output = json.dumps({
                "python": "3.14.0", "version": "0.805.0", "prefix": str(cwd / ".venv"),
                "extras": ["factories"], "requirements": [], "installed": {},
            })
        elif argv[-1] == CONFIG_PROBE:
            output = json.dumps({
                "database": "sqlite", "driver": "sqlite", "storage": "local",
                "storage_driver": "local", "name": "offline-example",
            })
        elif argv[-1] == "key:generate":
            path = cwd / ".env"
            content = path.read_text(encoding="utf-8").replace("APP_KEY=", "APP_KEY=test-key")
            path.write_text(content, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout=output)

    runner.run.side_effect = run_tool
    with patch("orionis_installer.cli.Runner", return_value=runner), patch(
        "orionis_installer.cli.check_prerequisites", return_value=prerequisites,
    ), patch(
        "orionis_installer.ui.output.framework_version", return_value="0.805.0",
    ):
        response = CliRunner().invoke(app, [
            "new", "offline-example", "--path", str(destination), "--no-interaction",
            "--no-color", "--git", "--no-migrate", "--no-open",
        ])
    assert response.exit_code == (1 if failure else 0), response.output
    assert (destination / ".env").is_file()
    assert not list(tmp_path.glob(".*.orionis-*"))
    if failure:
        assert response.output.count("preserved") > 0
    else:
        assert response.output.count("Application ready") > 0

