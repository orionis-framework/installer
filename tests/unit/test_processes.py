"""Verify executable isolation, owned process lifetimes and editor argument safety."""

from __future__ import annotations

import ctypes
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from orionis_installer import processes
from orionis_installer.exceptions import Cancelled, ProcessError
from orionis_installer.messages import MESSAGES
from orionis_installer.processes import (
    Runner,
    editor_command,
    isolated_environment,
    resolve_executable,
)


@pytest.mark.parametrize(
    "variable",
    [
        "UV_PROJECT_ENVIRONMENT",
        "UV_PROJECT",
        "UV_WORKING_DIR",
        "UV_CONFIG_FILE",
        "UV_ENV_FILE",
        "UV_PYTHON",
        "UV_SYSTEM_PYTHON",
        "UV_WORKSPACE",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "PYTHONHOME",
        "PYTHONPATH",
        "APP_KEY",
        "DB_PASSWORD",
        "AWS_SECRET_ACCESS_KEY",
        "ELECTRON_RUN_AS_NODE",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_COUNT",
        "BASH_ENV",
    ],
)
def test_environment_strips_redirects_and_application_settings(tmp_path, variable):
    """Verify environment strips redirects and application settings.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    variable : str
        Inherited environment key under test.
    """
    environment = isolated_environment({variable: "secret", "PATH": ""}, cwd=tmp_path)
    assert environment.get(variable) != "secret"


def test_environment_retains_network_and_download_policy_without_mutating_source(tmp_path):
    """Verify environment retains network and download policy without mutating source.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    original = {
        "HTTPS_PROXY": "https://user:private@proxy.example",
        "UV_INDEX_CORPORATE_PASSWORD": "secret-token",
        "UV_INDEX_URL": "https://index.example/simple",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_OFFLINE": "1",
        "SSL_CERT_FILE": "/certificate",
    }
    snapshot = original.copy()
    environment = isolated_environment(original, cwd=tmp_path)
    for variable, value in original.items():
        assert environment[variable] == value
    assert original == snapshot
    assert environment["NO_COLOR"] == "1"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"


def test_trusted_resolution_skips_current_relative_and_template_paths(tmp_path):
    """Verify trusted resolution skips current relative and template paths.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    working = tmp_path / "working"
    skeleton = tmp_path / "template"
    trusted = tmp_path / "trusted tools"
    for directory in [working, skeleton, trusted]:
        directory.mkdir()
    executable_name = "git.exe" if os.name == "nt" else "git"
    for directory in [working, skeleton, trusted]:
        executable = directory / executable_name
        executable.write_text("fixture")
        executable.chmod(0o755)
    path = os.pathsep.join(["", ".", str(working), str(skeleton), str(trusted)])
    assert (
        resolve_executable("git", working, path=path, excluded_roots=(skeleton,))
        == (trusted / executable_name).resolve()
    )
    assert isolated_environment({"PATH": path}, cwd=working, excluded_roots=(skeleton,))[
        "PATH"
    ] == str(trusted.resolve())


def test_resolution_rejects_executable_path_name(tmp_path):
    """Verify resolution rejects executable path name.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    with pytest.raises(ValueError):
        resolve_executable("../git", tmp_path)


def test_resolution_rejects_symlink_target_inside_untrusted_directory(tmp_path):
    """Verify resolution rejects symlink target inside untrusted directory.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    working = tmp_path / "project"
    trusted = tmp_path / "bin"
    working.mkdir()
    trusted.mkdir()
    filename = "uv.exe" if os.name == "nt" else "uv"
    target = working / filename
    target.write_text("fixture")
    target.chmod(0o755)
    try:
        (trusted / filename).symlink_to(target)
    except OSError:
        pytest.skip("This environment does not permit symlink creation.")
    assert resolve_executable("uv", working, path=str(trusted)) is None


def test_runner_uses_final_cwd_and_ignores_inherited_uvx_environment(tmp_path):
    """Verify that the runner uses the final directory and ignores inherited uvx settings.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    project = tmp_path / "project \u00f1 with spaces & $"
    project.mkdir()
    outside = tmp_path / "uvx environment"
    source = dict(os.environ)
    source.update(
        {
            "UV_PROJECT_ENVIRONMENT": str(outside),
            "VIRTUAL_ENV": str(outside),
            "PYTHONHOME": str(outside),
            "PYTHONPATH": str(outside),
            "APP_KEY": "secret",
        }
    )
    result = Runner(environ=source).run(
        [
            Path(sys.executable),
            "-I",
            "-c",
            "import json,os; print(json.dumps([os.getcwd(),"
            "os.getenv('UV_PROJECT_ENVIRONMENT'),os.getenv('VIRTUAL_ENV'),"
            "os.getenv('PYTHONPATH'),os.getenv('APP_KEY')]))",
        ],
        cwd=project,
        timeout=10,
    )
    assert json.loads(result.stdout) == [str(project.resolve()), None, None, None, None]
    assert not outside.exists()


def test_runner_never_interprets_metacharacters(tmp_path):
    """Verify runner never interprets metacharacters.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    dangerous = 'spaces \u00f1 & | > %TEMP% !x! ^ " quotes ${APP_KEY}'
    result = Runner().run(
        [
            Path(sys.executable),
            "-I",
            "-c",
            "import json,sys; print(json.dumps(sys.argv[1:]))",
            dangerous,
        ],
        cwd=tmp_path,
    )
    assert json.loads(result.stdout) == [dangerous]


def test_errors_omit_secrets_and_raw_output(tmp_path, capsys):
    """Verify errors omit secrets and raw output.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    capsys : pytest.CaptureFixture
        Captured terminal streams used to check output secrecy.
    """
    secret = "password-42 private-token secret-app-key"
    with pytest.raises(ProcessError) as raised:
        Runner().run(
            [
                Path(sys.executable),
                "-I",
                "-c",
                "import sys; print(sys.argv[1]); print(sys.argv[1],file=sys.stderr); sys.exit(7)",
                secret,
            ],
            cwd=tmp_path,
        )
    assert str(raised.value) == MESSAGES["process_exit_failed"].format(code=7)
    assert secret not in str(raised.value)
    assert secret not in repr(raised.value)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("argv", ["python --version", [], ["python", "--version"]])
def test_runner_requires_separate_arguments_and_absolute_native_executable(tmp_path, argv):
    """Verify that the runner requires separate arguments and an absolute native executable.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    argv : Sequence[str or Path]
        Separate command arguments supplied to the process fixture.
    """
    with pytest.raises((ValueError, ProcessError)):
        Runner().run(argv, cwd=tmp_path)


def test_runner_refuses_batch_files_without_native_adapter(tmp_path):
    """Verify that the runner requires a native adapter for batch files.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    with pytest.raises(ProcessError):
        Runner().run([tmp_path / "code.cmd", str(tmp_path)], cwd=tmp_path)


def test_runner_check_false_returns_failure_for_internal_classification(tmp_path):
    """Verify that disabled exit checking returns failure for internal classification.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    result = Runner().run(
        [Path(sys.executable), "-I", "-c", "raise SystemExit(9)"], cwd=tmp_path, check=False
    )
    assert result.returncode == 9


def test_timeout_stops_and_reaps_process(tmp_path):
    """Verify timeout stops and reaps process.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    with pytest.raises(
        ProcessError, match=re.escape(MESSAGES["process_timeout"].format(timeout=0.2))
    ):
        Runner().run(
            [Path(sys.executable), "-I", "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            timeout=0.2,
        )


def _is_process_running(pid: int) -> bool:
    """Inspect whether an owned process remains active.

    Parameters
    ----------
    pid : int
        Owned fixture process identifier to inspect.

    Returns
    -------
    bool
        Whether the inspected process is active rather than exited or a zombie.
    """
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = wintypes.DWORD()
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    state = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, check=False
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def test_timeout_stops_owned_descendant_even_if_leader_exits(tmp_path):
    """Verify timeout stops owned descendant even if leader exits.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess,sys; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)']); "
        f"Path({str(pid_file)!r}).write_text(str(child.pid))"
    )
    with pytest.raises(
        ProcessError, match=re.escape(MESSAGES["process_timeout"].format(timeout=1))
    ):
        Runner().run([Path(sys.executable), "-I", "-c", script], cwd=tmp_path, timeout=1)
    assert pid_file.exists(), "The fixture must have created its child process."
    assert not _is_process_running(int(pid_file.read_text()))


def test_keyboard_interrupt_stops_child_and_has_cancelled_classification(tmp_path, monkeypatch):
    """Verify keyboard interrupt stops child and has cancelled classification.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    monkeypatch : pytest.MonkeyPatch
        Scoped replacement fixture for controlled dependencies.
    """
    child = Mock()
    child.communicate.side_effect = KeyboardInterrupt
    child.poll.return_value = None
    monkeypatch.setattr(processes.subprocess, "Popen", Mock(return_value=child))
    job = Mock()
    monkeypatch.setattr(processes, "_WindowsJob", Mock(return_value=job))
    monkeypatch.setattr(processes, "_resume_windows_process", Mock())
    stop = Mock()
    monkeypatch.setattr(Runner, "_stop", stop)
    with pytest.raises(Cancelled) as raised:
        Runner().run([Path(sys.executable), "-I", "-c", "pass"], cwd=tmp_path)
    stop.assert_called_once()
    assert raised.value.exit_code == 130


@pytest.mark.parametrize("versioned", [False, True])
def test_windows_code_cmd_adapter_uses_native_argv_for_spaces_and_metacharacters(
    tmp_path, versioned
):
    """Verify Windows Code batch adapter uses native argv for spaces and metacharacters.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    versioned : bool
        Whether the VS Code fixture uses a versioned resources directory.
    """
    installation = tmp_path / "VS Code installation"
    executable = installation / "Code.exe"
    wrapper = installation / "bin" / "code.cmd"
    resource_relative = (
        "release123/resources/app/out/cli.js" if versioned else "resources/app/out/cli.js"
    )
    cli = installation / resource_relative
    cli.parent.mkdir(parents=True)
    wrapper.parent.mkdir()
    executable.write_text("native fixture")
    cli.write_text("cli fixture")
    windows_resource = resource_relative.replace("/", "\\")
    wrapper.write_text(
        "@echo off\nsetlocal\nset ELECTRON_RUN_AS_NODE=1\n"
        f'"%~dp0..\\Code.exe" "%~dp0..\\{windows_resource}" %*\nendlocal\n'
    )
    project = tmp_path / "project \u00f1 & %PATH% !bang! ^ $ space"
    arguments, environment = editor_command(wrapper, project)
    assert arguments == [
        str(executable.resolve()),
        str(cli.resolve()),
        "--new-window",
        str(project.resolve()),
    ]
    assert environment == {"ELECTRON_RUN_AS_NODE": "1"}
    assert not any("cmd.exe" in argument for argument in arguments)


def test_code_cmd_adapter_refuses_paths_outside_installation(tmp_path):
    """Verify Code batch adapter refuses paths outside installation.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    installation = tmp_path / "vscode"
    wrapper = installation / "bin" / "code.cmd"
    wrapper.parent.mkdir(parents=True)
    outside = tmp_path / "Code.exe"
    outside.write_text("fixture")
    wrapper.write_text('"%~dp0..\\..\\Code.exe" "%~dp0..\\resources\\app\\out\\cli.js" %*')
    with pytest.raises(ProcessError):
        editor_command(wrapper, tmp_path)


def test_code_cmd_adapter_refuses_shell_substitutions(tmp_path):
    """Verify Code batch adapter refuses shell substitutions.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    wrapper = tmp_path / "bin" / "code.cmd"
    wrapper.parent.mkdir()
    wrapper.write_text('"%~dp0..\\%EVIL%\\Code.exe" "%~dp0..\\resources\\app\\out\\cli.js" %*')
    with pytest.raises(ProcessError):
        editor_command(wrapper, tmp_path)


def test_posix_editor_receives_path_as_one_argument(tmp_path):
    """Verify POSIX editor receives path as one argument.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    arguments, environment = editor_command(tmp_path / "code", tmp_path / "space & \u00f1")
    assert arguments == [
        str((tmp_path / "code").resolve()),
        "--new-window",
        str((tmp_path / "space & \u00f1").resolve()),
    ]
    assert environment == {}


def test_environment_disables_global_git_rewrites_filters_and_hooks(tmp_path):
    """Verify that child Git ignores global rewrites, filters and hooks.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    environment = isolated_environment(
        {"GIT_CONFIG_GLOBAL": "untrusted", "GIT_CONFIG_NOSYSTEM": "0"}, cwd=tmp_path
    )
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_KEY_0"] == "core.hooksPath"
    assert environment["GIT_CONFIG_VALUE_0"] == os.devnull


@pytest.mark.skipif(
    os.name != "nt", reason="Process suspension and job objects are Windows-specific."
)
def test_windows_creates_suspended_assigns_job_then_resumes(tmp_path, monkeypatch):
    """Verify that a Windows child receives its private job before execution.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    monkeypatch : pytest.MonkeyPatch
        Scoped replacement fixture for controlled dependencies.
    """
    order = []
    child = Mock()
    child.communicate.return_value = ("", "")
    child.returncode = 0
    job = Mock()

    def spawn(argv, **kwargs):
        """Simulate creation of an owned process.

        Parameters
        ----------
        argv : Sequence[str or Path]
            Separate command arguments supplied to the process fixture.
        **kwargs : dict
            Additional options accepted by the controlled fixture.

        Returns
        -------
        Mock
            Owned child process fixture.
        """
        assert kwargs["creationflags"] & 0x4  # CREATE_SUSPENDED
        assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
        order.append("spawn-suspended")
        return child

    def assign(process):
        """Record assignment of the owned child to its Windows job.

        Parameters
        ----------
        process : subprocess.Popen
            Owned child fixture whose lifecycle is being checked.

        Returns
        -------
        Mock
            Private job fixture assigned to the owned child.
        """
        assert process is child
        order.append("assign-job")
        return job

    def resume(process):
        """Record resumption of the owned primary thread.

        Parameters
        ----------
        process : subprocess.Popen
            Owned child fixture whose lifecycle is being checked.
        """
        assert process is child
        order.append("resume-owned-primary-thread")

    monkeypatch.setattr(processes.subprocess, "Popen", spawn)
    monkeypatch.setattr(processes, "_WindowsJob", assign)
    monkeypatch.setattr(processes, "_resume_windows_process", resume)
    Runner().run([Path(sys.executable), "-I", "-c", "pass"], cwd=tmp_path)
    assert order == ["spawn-suspended", "assign-job", "resume-owned-primary-thread"]


@pytest.mark.skipif(
    os.name != "nt", reason="Process suspension and job objects are Windows-specific."
)
def test_windows_resume_failure_terminates_and_waits_owned_process(tmp_path, monkeypatch):
    """Verify that a failed Windows resume terminates and reaps the owned child.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    monkeypatch : pytest.MonkeyPatch
        Scoped replacement fixture for controlled dependencies.
    """
    child = Mock()
    job = Mock()
    monkeypatch.setattr(processes.subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(processes, "_WindowsJob", Mock(return_value=job))
    monkeypatch.setattr(
        processes, "_resume_windows_process", Mock(side_effect=OSError("fixture failure"))
    )
    stop = Mock()
    monkeypatch.setattr(Runner, "_stop", stop)
    with pytest.raises(ProcessError, match=re.escape(MESSAGES["process_group_failed"])):
        Runner().run([Path(sys.executable), "-I", "-c", "pass"], cwd=tmp_path)
    stop.assert_called_once_with(child, job)


def test_interrupt_during_spawn_is_delivered_after_ownership_and_restores_handler(
    tmp_path, monkeypatch
):
    """Verify interrupt during spawn is delivered after ownership and restores handler.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    monkeypatch : pytest.MonkeyPatch
        Scoped replacement fixture for controlled dependencies.
    """
    child = Mock()
    job = Mock()
    previous = signal.getsignal(signal.SIGINT)

    def spawn(*args, **kwargs):
        """Raise Ctrl+C while returning a newly created owned process.

        Parameters
        ----------
        *args : tuple
            Additional positional arguments forwarded by the fixture.
        **kwargs : dict
            Additional options accepted by the controlled fixture.

        Returns
        -------
        Mock
            Owned child process fixture.
        """
        signal.raise_signal(signal.SIGINT)
        return child

    monkeypatch.setattr(processes.subprocess, "Popen", spawn)
    assigned = Mock(return_value=job)
    monkeypatch.setattr(processes, "_WindowsJob", assigned)
    monkeypatch.setattr(processes, "_resume_windows_process", Mock())
    stop = Mock()
    monkeypatch.setattr(Runner, "_stop", stop)
    with pytest.raises(Cancelled):
        Runner().run([Path(sys.executable), "-I", "-c", "pass"], cwd=tmp_path)
    stop.assert_called_once()
    assert stop.call_args.args[0] is child
    if os.name == "nt":
        assigned.assert_called_once_with(child)
        assert stop.call_args.args[1] is job
    assert signal.getsignal(signal.SIGINT) == previous
    child.communicate.assert_not_called()
