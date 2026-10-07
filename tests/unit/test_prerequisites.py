"""Verify trusted executable discovery and Python download policy handling."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from orionis_installer import prerequisites
from orionis_installer.exceptions import Cancelled, PrerequisiteError, ProcessError
from orionis_installer.messages import MESSAGES
from orionis_installer.prerequisites import check_prerequisites, verify_python


class FakeRunner:
    """Simulate uv discovery and Python probing without external processes."""

    def __init__(self, *, missing_python=False, version=None, install_fails=False, environ=None):
        """Initialize controlled uv discovery and Python version responses.

        Parameters
        ----------
        missing_python : bool, optional
            Simulate missing Python until the fixture installation succeeds.
        version : object, optional
            Simulated Python version probe response.
        install_fails : bool, optional
            Simulate an unsuccessful managed Python installation.
        environ : Mapping[str, str] or None, optional
            Inherited environment snapshot used by the fixture.
        """
        self.environ = {} if environ is None else environ
        self.calls = []
        self.missing_python = missing_python
        self.installed = False
        self.install_fails = install_fails
        self.version = [3, 14, 6, "final"] if version is None else version

    def run(self, argv, *, cwd, timeout=300, env=None, check=True):
        """Simulate prerequisite commands and record isolated execution options.

        Parameters
        ----------
        argv : Sequence[str or Path]
            Separate command arguments supplied to the process fixture.
        cwd : Path
            Explicit working directory recorded by the process fixture.
        timeout : float, optional
            Execution limit recorded by the process fixture.
        env : Mapping[str, str] or None, optional
            Explicit child environment recorded by the fixture.
        check : bool, optional
            Requested exit-code checking behavior recorded by the fixture.

        Returns
        -------
        subprocess.CompletedProcess[str]
            Controlled uv discovery, installation or version probe response.

        Raises
        ------
        ProcessError
            If the configured managed Python installation should fail.
        """
        arguments = [str(argument) for argument in argv]
        self.calls.append((arguments, Path(cwd), timeout, env, check))
        if arguments[1:3] == ["python", "find"]:
            absent = self.missing_python and not self.installed
            return subprocess.CompletedProcess(
                arguments, 1 if absent else 0, "" if absent else sys.executable + "\n", ""
            )
        if arguments[1:3] == ["python", "install"]:
            if self.install_fails:
                raise ProcessError("Fixture operation failed")
            self.installed = True
        if "-c" in arguments:
            return subprocess.CompletedProcess(arguments, 0, json.dumps(self.version), "")
        return subprocess.CompletedProcess(arguments, 0, "fixture version", "")


@pytest.fixture
def resolved_tools(tmp_path, monkeypatch):
    """Patch executable discovery with disposable uv and Git paths.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    monkeypatch : pytest.MonkeyPatch
        Scoped replacement fixture for controlled dependencies.

    Returns
    -------
    dict[str, Path]
        Disposable uv and Git discovery paths.
    """
    tools = {"uv": tmp_path / "tools" / "uv.exe", "git": tmp_path / "tools" / "git.exe"}
    monkeypatch.setattr(
        prerequisites, "resolve_executable", lambda name, cwd, **kwargs: tools[name]
    )
    return tools


def test_prerequisites_verify_actual_python_with_no_installer_interpreter_assumption(
    tmp_path, resolved_tools
):
    """Verify application Python independently of the installer interpreter.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    resolved_tools : dict[str, Path or None]
        Patched uv and Git discovery results.
    """
    runner = FakeRunner()
    result = check_prerequisites(runner, cwd=tmp_path / "project")
    assert result.uv == resolved_tools["uv"]
    assert result.git == resolved_tools["git"]
    assert result.python == Path(sys.executable).resolve()
    assert result.python_version == "3.14.6"
    find_calls = [call for call in runner.calls if call[0][1:3] == ["python", "find"]]
    assert find_calls[0][0][3:] == ["3.14", "--no-project", "--no-python-downloads"]
    assert find_calls[0][1] != tmp_path / "project"
    probe = runner.calls[-1][0]
    assert probe[1:4] == ["-I", "-B", "-c"]
    assert not any("--system" in call[0] or "--active" in call[0] for call in runner.calls)


@pytest.mark.parametrize("missing", ["uv", "git"])
def test_missing_uv_or_git_is_actionable_and_runs_nothing(tmp_path, resolved_tools, missing):
    """Verify missing uv or Git is actionable and runs nothing.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    resolved_tools : dict[str, Path or None]
        Patched uv and Git discovery results.
    missing : str
        Required tool or skeleton filename removed for this case.
    """
    resolved_tools[missing] = None
    runner = FakeRunner()
    with pytest.raises(PrerequisiteError) as raised:
        check_prerequisites(runner, cwd=tmp_path)
    assert missing.lower() in str(raised.value).lower()
    assert "https://" in str(raised.value)
    assert raised.value.exit_code == 2
    assert not runner.calls


def test_missing_python_installs_with_uv_without_global_links_or_registry(tmp_path, resolved_tools):
    """Verify missing Python installs with uv without global links or registry.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    resolved_tools : dict[str, Path or None]
        Patched uv and Git discovery results.
    """
    runner = FakeRunner(missing_python=True)
    messages = []
    check_prerequisites(runner, cwd=tmp_path, announce=messages.append)
    install = next(call for call in runner.calls if call[0][1:3] == ["python", "install"])
    assert install[0][3:] == ["3.14", "--no-bin", "--no-registry"]
    assert messages == [MESSAGES["python_download_announcement"]]
    assert not (tmp_path / ".venv").exists()


@pytest.mark.parametrize("environment", [{"UV_PYTHON_DOWNLOADS": "never"}, {"UV_OFFLINE": "1"}])
def test_missing_python_respects_disabled_downloads_and_network(
    tmp_path, resolved_tools, environment
):
    """Verify missing Python respects disabled downloads and network.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    resolved_tools : dict[str, Path or None]
        Patched uv and Git discovery results.
    environment : dict[str, str]
        Download or offline policy supplied to the fixture.
    """
    runner = FakeRunner(missing_python=True, environ=environment)
    with pytest.raises(PrerequisiteError):
        check_prerequisites(runner, cwd=tmp_path)
    assert not any(call[0][1:3] == ["python", "install"] for call in runner.calls)


def test_uv_python_install_failure_becomes_prerequisite_error(tmp_path, resolved_tools):
    """Verify uv Python install failure becomes prerequisite error.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    resolved_tools : dict[str, Path or None]
        Patched uv and Git discovery results.
    """
    runner = FakeRunner(missing_python=True, install_fails=True)
    with pytest.raises(PrerequisiteError, match=re.escape(MESSAGES["python_download_failed"])):
        check_prerequisites(runner, cwd=tmp_path)


@pytest.mark.parametrize(
    "version",
    [
        [3, 13, 9, "final"],
        [3, 15, 0, "final"],
        [3, 14, 0, "candidate"],
        [3, 14, "6", "final"],
        {},
        [],
    ],
)
def test_python_requires_actual_stable_314_version(tmp_path, version):
    """Verify that the version probe accepts only stable Python 3.14.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    version : object, optional
        Simulated Python version probe response.
    """
    with pytest.raises(PrerequisiteError):
        verify_python(FakeRunner(version=version), Path(sys.executable), cwd=tmp_path)


def test_python_probe_failure_is_not_reported_as_compatible(tmp_path):
    """Verify Python probe failure is not reported as compatible.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """

    class BrokenRunner:
        """Simulate a failed Python version probe containing private output."""

        def run(self, argv, **kwargs):
            """Fail the version probe with a diagnostic containing a private value.

            Parameters
            ----------
            argv : Sequence[str or Path]
                Separate command arguments supplied to the process fixture.
            **kwargs : dict
                Additional options accepted by the controlled fixture.

            Raises
            ------
            ProcessError
                Always raise the fixture probe failure.
            """
            raise ProcessError("fixture failure with private secret")

    with pytest.raises(PrerequisiteError) as raised:
        verify_python(BrokenRunner(), Path(sys.executable), cwd=tmp_path)
    assert "private secret" not in str(raised.value)


def test_cancellation_is_never_interpreted_as_missing_python(tmp_path, resolved_tools):
    """Verify that cancellation stops prerequisite discovery immediately.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    resolved_tools : dict[str, Path or None]
        Patched uv and Git discovery results.
    """

    class CancelledRunner(FakeRunner):
        """Simulate cancellation during prerequisite discovery."""

        def run(self, argv, **kwargs):
            """Cancel Python discovery while delegating other prerequisite commands.

            Parameters
            ----------
            argv : Sequence[str or Path]
                Separate command arguments supplied to the process fixture.
            **kwargs : dict
                Additional options accepted by the controlled fixture.

            Returns
            -------
            subprocess.CompletedProcess[str]
                Controlled response for commands that precede Python discovery.

            Raises
            ------
            Cancelled
                If the command starts Python discovery.
            """
            if str(argv[1]) == "python":
                raise Cancelled("fixture cancellation")
            return super().run(argv, **kwargs)

    with pytest.raises(Cancelled):
        check_prerequisites(CancelledRunner(), cwd=tmp_path)


def test_neutral_probe_environment_drops_workspace_redirects(tmp_path, resolved_tools):
    """Verify neutral probe environment drops workspace redirects.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    resolved_tools : dict[str, Path or None]
        Patched uv and Git discovery results.
    """
    runner = FakeRunner(
        environ={
            "UV_PROJECT_ENVIRONMENT": str(tmp_path / "outside"),
            "UV_PROJECT": "other",
            "UV_CONFIG_FILE": "",
            "UV_ENV_FILE": "other.env",
            "APP_KEY": "secret",
            "UV_PYTHON_DOWNLOADS": "automatic",
            "UV_INDEX_URL": "https://example/simple",
        }
    )
    check_prerequisites(runner, cwd=tmp_path)
    environment = next(call[3] for call in runner.calls if call[0][1:3] == ["python", "find"])
    assert environment["UV_PYTHON_DOWNLOADS"] == "automatic"
    assert environment["UV_INDEX_URL"] == "https://example/simple"
    for key in ("UV_PROJECT_ENVIRONMENT", "UV_PROJECT", "UV_CONFIG_FILE", "UV_ENV_FILE", "APP_KEY"):
        assert key not in environment


def test_python_result_cannot_be_an_untrusted_project_executable(tmp_path, resolved_tools):
    """Verify Python result cannot be an untrusted project executable.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    resolved_tools : dict[str, Path or None]
        Patched uv and Git discovery results.
    """
    local = tmp_path / "python.exe"
    local.write_text("untrusted fixture")

    class UntrustedRunner(FakeRunner):
        """Simulate discovery of an executable inside the caller directory."""

        def run(self, argv, **kwargs):
            """Return a caller-directory interpreter from simulated Python discovery.

            Parameters
            ----------
            argv : Sequence[str or Path]
                Separate command arguments supplied to the process fixture.
            **kwargs : dict
                Additional options accepted by the controlled fixture.

            Returns
            -------
            subprocess.CompletedProcess[str]
                Probe response containing an untrusted local executable path.
            """
            if list(argv)[1:3] == ["python", "find"]:
                return subprocess.CompletedProcess(argv, 0, str(local), "")
            return super().run(argv, **kwargs)

    with pytest.raises(
        PrerequisiteError, match=re.escape(MESSAGES["python_working_directory_invalid"])
    ):
        check_prerequisites(UntrustedRunner(), cwd=tmp_path)
