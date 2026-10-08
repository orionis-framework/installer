from __future__ import annotations
import ctypes
import os
import re
import signal
import subprocess
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from orionis_installer.messages import MESSAGES
from .exceptions import Cancelled, ProcessError

# These are OS, locale, network/certificate and uv index/download settings, not
# application settings. UV_PROJECT*, UV_WORKING_DIR, UV_CONFIG_FILE, UV_PYTHON,
# VIRTUAL_ENV, CONDA_PREFIX, PYTHONPATH, PYTHONHOME and arbitrary app/cloud variables
# are deliberately absent to keep application settings out of child processes.
_ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "GIT_SSL_CAINFO",
        "UV_SYSTEM_CERTS",
        "UV_NATIVE_TLS",
        "UV_OFFLINE",
        "UV_PYTHON_DOWNLOADS",
        "UV_PYTHON_PREFERENCE",
        "UV_MANAGED_PYTHON",
        "UV_NO_MANAGED_PYTHON",
        "UV_PYTHON_INSTALL_DIR",
        "UV_PYTHON_INSTALL_MIRROR",
        "UV_PYPY_INSTALL_MIRROR",
        "UV_ASTRAL_MIRROR_URL",
        "UV_CACHE_DIR",
        "UV_NO_CACHE",
        "UV_HTTP_TIMEOUT",
        "UV_REQUEST_TIMEOUT",
        "UV_HTTP_CONNECT_TIMEOUT",
        "UV_HTTP_RETRIES",
        "UV_INDEX",
        "UV_DEFAULT_INDEX",
        "UV_INDEX_URL",
        "UV_EXTRA_INDEX_URL",
        "UV_INDEX_STRATEGY",
        "UV_NO_INDEX",
        "UV_FIND_LINKS",
        "UV_INSECURE_HOST",
    }
)
_INDEX_CREDENTIAL = re.compile(r"UV_INDEX_[A-Z0-9_]+_(?:USERNAME|PASSWORD)\Z")

def _within(path: Path, root: Path) -> bool:
    """
    Check whether a path equals or descends from a controlled root.

    Parameters
    ----------
    path : Path
        Path to inspect.
    root : Path
        Root delimiting the allowed subtree.

    Returns
    -------
    bool
        Whether the path belongs to the subtree.
    """
    return path == root or root in path.parents

def _trusted_path(path: str, cwd: Path, excluded_roots: Sequence[Path] = ()) -> str:  # NOSONAR
    """
    Filter executable search entries outside controlled exclusions.

    Parameters
    ----------
    path : str
        Search path separated by the platform's path separator.
    cwd : Path
        Current directory to exclude from implicit executable lookup.
    excluded_roots : Sequence[Path], optional
        Untrusted subtrees to exclude completely.

    Returns
    -------
    str
        Search path containing resolved absolute entries.
    """
    current_directory = cwd.resolve()
    roots = [root.resolve() for root in excluded_roots]
    entries: list[str] = []
    for raw in path.split(os.pathsep):
        if not raw:
            continue
        candidate = Path(raw.strip('"'))
        if not candidate.is_absolute():
            continue
        resolved = candidate.resolve()
        if resolved == current_directory or any(_within(resolved, root) for root in roots):
            continue
        entries.append(str(resolved))
    return os.pathsep.join(entries)

def isolated_environment(
    base: Mapping[str, str] | None = None,
    *,
    cwd: Path | None = None,
    excluded_roots: Sequence[Path] = (),
) -> dict[str, str]:
    """
    Build an isolated child environment without loading dotenv files.

    Named uv index credentials are retained for authenticated indexes but never
    printed. Environment keys are matched case insensitively on Windows.

    Parameters
    ----------
    base : Mapping[str, str] or None, optional
        Environment snapshot; use the current environment when omitted.
    cwd : Path or None, optional
        Working directory excluded from executable search.
    excluded_roots : Sequence[Path], optional
        Untrusted directory trees excluded from executable search.

    Returns
    -------
    dict[str, str]
        Allowlisted network, OS and uv settings with fixed isolation flags.
    """
    source = os.environ if base is None else base
    result: dict[str, str] = {}
    for key, value in source.items():
        normalized = key.upper()
        if normalized in _ENVIRONMENT_ALLOWLIST or _INDEX_CREDENTIAL.fullmatch(normalized):
            result[key if os.name != "nt" else normalized] = value
    path_key = next((key for key in result if key.upper() == "PATH"), "PATH")
    result[path_key] = _trusted_path(result.get(path_key, ""), cwd or Path.cwd(), excluded_roots)
    result.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "UV_NO_PROGRESS": "1",
            "UV_COLOR": "never",
            "NO_COLOR": "1",
            "GIT_TERMINAL_PROMPT": "0",
            # A fixed official URL must not be rewritten by global Git config;
            # globally registered smudge filters must not execute template code.
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            # Do not run a globally configured Git template hook during git init.
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": os.devnull,
            "GIT_CONFIG_KEY_1": "init.templateDir",
            "GIT_CONFIG_VALUE_1": "",
        }
    )
    return result

def resolve_executable( # NOSONAR
    name: str,
    cwd: Path,
    *,
    path: str | None = None,
    excluded_roots: Sequence[Path] = (),
) -> Path | None:
    """
    Search absolute PATH entries, never cwd or a downloaded skeleton.

    Unlike shutil.which on Windows, this never prepends the current directory.
    A symlink whose target enters an excluded root is also rejected.

    Parameters
    ----------
    name : str
        Simple executable name without directory separators.
    cwd : Path
        Current directory excluded from lookup.
    path : str or None, optional
        Explicit search path; use the inherited PATH when omitted.
    excluded_roots : Sequence[Path], optional
        Untrusted directory trees excluded from lookup.

    Returns
    -------
    Path or None
        Resolved executable path, or None when no trusted candidate exists.

    Raises
    ------
    ValueError
        If the executable name includes a directory component.
    """
    if Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError(MESSAGES["executable_name_invalid"])
    source_path = path if path is not None else os.environ.get("PATH", "")
    current_directory = cwd.resolve()
    roots = [root.resolve() for root in excluded_roots]
    extensions = (".exe", ".com", ".cmd", ".bat") if os.name == "nt" else ("",)
    names = [name] if Path(name).suffix else [name + suffix for suffix in extensions]
    for directory in _trusted_path(source_path, cwd, excluded_roots).split(os.pathsep):
        if not directory:
            continue
        for filename in names:
            candidate = Path(directory, filename)
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if resolved.parent == current_directory or any(
                _within(resolved, root) for root in roots
            ):
                continue
            if os.name == "nt" or os.access(resolved, os.X_OK):
                return resolved
    return None

def editor_command(launcher: Path, project: Path) -> tuple[list[str], dict[str, str]]:
    """
    Adapt the official VS Code batch wrapper without invoking cmd.exe.

    The wrapper's quoted %~dp0 paths are read as data. Only the installed native
    Code executable and its cli.js within the same installation are accepted.
    This also supports VS Code's versioned resources directory on Windows.

    Parameters
    ----------
    launcher : Path
        Trusted native launcher or official Windows batch wrapper.
    project : Path
        Project directory to open in a new editor window.

    Returns
    -------
    tuple[list[str], dict[str, str]]
        Native argument vector and explicit Electron environment overrides.

    Raises
    ------
    ProcessError
        If a batch wrapper cannot be mapped to its installed native CLI.
    """
    launcher = launcher.resolve()
    project = project.resolve()
    if launcher.suffix.lower() not in {".cmd", ".bat"}:
        return [str(launcher), "--new-window", str(project)], {}
    installation = launcher.parent.parent
    try:
        wrapper = launcher.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise ProcessError(MESSAGES["editor_wrapper_unreadable"]) from error
    pair = re.search(r'^"%~dp0([^"\r\n]+)"\s+"%~dp0([^"\r\n]+)"\s+%\*\s*$', wrapper, re.MULTILINE)
    if pair is None:
        raise ProcessError(MESSAGES["editor_wrapper_incompatible"])
    paths: list[Path] = []
    for relative in pair.groups():
        # Percent/ampersand substitutions are never interpreted as paths.
        if any(character in relative for character in "%!&|<>^\x00"):
            raise ProcessError(MESSAGES["editor_wrapper_ambiguous"])
        candidate = (launcher.parent / relative.replace("\\", "/")).resolve()
        if not _within(candidate, installation) or not candidate.is_file():
            raise ProcessError(MESSAGES["editor_wrapper_outside"])
        paths.append(candidate)
    executable, cli = paths
    if executable.name.lower() not in {"code.exe", "code - insiders.exe"} or tuple(
        component.lower() for component in cli.parts[-4:]
    ) != ("resources", "app", "out", "cli.js"):
        raise ProcessError(MESSAGES["editor_components_missing"])
    return [str(executable), str(cli), "--new-window", str(project)], {"ELECTRON_RUN_AS_NODE": "1"}

class _WindowsJob:
    """Own Windows descendants independently of their process leader."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        """
        Assign a suspended child to a private job with bounded lifetime.

        Parameters
        ----------
        process : subprocess.Popen[str]
            Newly created suspended process owned by the runner.

        Raises
        ------
        OSError
            If Windows job creation, configuration or assignment fails.
        """
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            """Represent the native basic job limits structure."""

            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t),
                ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class Iocounters(ctypes.Structure):
            """Represent native job input and output counters."""

            _fields_ = [
                (name, ctypes.c_uint64)
                for name in ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")
            ]

        class ExtendedLimits(ctypes.Structure):
            """Combine native job lifetime and resource limits."""

            _fields_ = [
                ("basic", BasicLimits),
                ("io", Iocounters),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        windows_library = getattr(ctypes, "WinDLL", None)
        if windows_library is None:
            raise OSError(MESSAGES["windows_api_unavailable"])
        self.kernel = windows_library("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        self.limits = ExtendedLimits()
        self.limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (
            not self.handle
            or not self.kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(self.limits), ctypes.sizeof(self.limits)
            )
            or not self.kernel.AssignProcessToJobObject(
                self.handle, int(getattr(process, "_handle", 0))
            )
        ):
            if self.handle:
                self.kernel.CloseHandle(self.handle)
            self.handle = None
            raise OSError(MESSAGES["windows_process_isolation_failed"])

    def close(self, *, terminate: bool = True) -> None:
        """
        Release the job and optionally terminate its owned descendants.

        Parameters
        ----------
        terminate : bool, optional
            Preserve detached editor children when False.
        """
        if self.handle:
            if not terminate:
                self.limits.basic.flags = 0
                self.kernel.SetInformationJobObject(
                    self.handle, 9, ctypes.byref(self.limits), ctypes.sizeof(self.limits)
                )
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _resume_windows_process(process: subprocess.Popen[str]) -> None:
    """
    Resume the sole primary thread of our newly created suspended process.

    Popen closes the CreateProcess thread handle. Toolhelp obtains its thread ID;
    GetProcessIdOfThread verifies ownership before resuming. No process code runs
    until its job has already been assigned, eliminating the child-spawn race.

    Parameters
    ----------
    process : subprocess.Popen[str]
        Newly created child already assigned to the runner's private job.

    Raises
    ------
    OSError
        If the sole owned primary thread cannot be verified and resumed.
    """
    from ctypes import wintypes

    class ThreadEntry(ctypes.Structure):
        """Represent an entry in a native thread snapshot."""

        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("thread_id", wintypes.DWORD),
            ("owner_pid", wintypes.DWORD),
            ("base_priority", wintypes.LONG),
            ("delta_priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
        ]

    windows_library = getattr(ctypes, "WinDLL", None)
    if windows_library is None:
        raise OSError(MESSAGES["windows_api_unavailable"])
    kernel = windows_library("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32First.restype = wintypes.BOOL
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32Next.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.GetProcessIdOfThread.argtypes = [wintypes.HANDLE]
    kernel.GetProcessIdOfThread.restype = wintypes.DWORD
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise OSError(MESSAGES["windows_threads_enumeration_failed"])
    identifiers: list[int] = []
    try:
        entry = ThreadEntry()
        entry.size = ctypes.sizeof(entry)
        present = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while present:
            if entry.owner_pid == process.pid:
                identifiers.append(entry.thread_id)
            entry.size = ctypes.sizeof(entry)
            present = kernel.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    if len(identifiers) != 1:
        raise OSError(MESSAGES["windows_primary_thread_missing"])
    thread = kernel.OpenThread(0x0802, False, identifiers[0])
    if not thread:
        raise OSError(MESSAGES["windows_thread_open_failed"])
    try:
        if kernel.GetProcessIdOfThread(thread) != process.pid or kernel.ResumeThread(thread) != 1:
            raise OSError(MESSAGES["windows_thread_resume_failed"])
    finally:
        kernel.CloseHandle(thread)

@contextmanager
def _defer_spawn_interrupt() -> Iterator[None]:
    """
    Defer Ctrl+C until the newly spawned process has an owned lifetime.

    Yields
    ------
    None
        Guarded spawn interval with the previous signal handler preserved.

    Raises
    ------
    KeyboardInterrupt
        After ownership is established if Ctrl+C arrived during spawning.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGINT)
    interrupted = False

    def remember_interrupt(signum: int, frame: object) -> None:
        """Record an interrupt without interrupting child ownership setup.

        Parameters
        ----------
        signum : int
            Delivered interrupt signal number.
        frame : object
            Interpreter frame supplied by the signal machinery.
        """
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, remember_interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
        if interrupted:
            raise KeyboardInterrupt

class Runner:
    """Capture tools without exposing their output, and reap owned children."""

    def __init__(self, *, environ: Mapping[str, str] | None = None) -> None:
        """
        Snapshot the environment used to prepare isolated child processes.

        Parameters
        ----------
        environ : Mapping[str, str] or None, optional
            Environment snapshot; use the current environment when omitted.
        """
        self.environ = dict(os.environ if environ is None else environ)

    def run(
        self,
        argv: Sequence[str | Path],
        *,
        cwd: Path,
        timeout: float = 300,
        env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """
        Run a native process and capture output for internal parsing.

        Parameters
        ----------
        argv : Sequence[str or Path]
            Separate arguments beginning with an absolute native executable.
        cwd : Path
            Explicit existing working directory.
        timeout : float, optional
            Maximum execution time in seconds.
        env : Mapping[str, str] or None, optional
            Explicit trusted overrides applied after environment isolation.
        check : bool, optional
            Raise an error when the child exits unsuccessfully.

        Returns
        -------
        subprocess.CompletedProcess[str]
            Captured result whose output must not be displayed without review.

        Raises
        ------
        ProcessError
            If startup, child ownership, timeout or checked execution fails.
        Cancelled
            If the user interrupts the operation.
        ValueError
            If the arguments, directory or timeout are invalid.
        """
        return self._run(argv, cwd=cwd, timeout=timeout, env=env, check=check)

    def openEditor(
        self, launcher: Path, project: Path, *, cwd: Path, timeout: float = 30
    ) -> subprocess.CompletedProcess[str]:
        """
        Launch VS Code without waiting for its editor window to close.

        Parameters
        ----------
        launcher : Path
            Trusted editor launcher resolved before project execution.
        project : Path
            Directory to open in a new window.
        cwd : Path
            Explicit working directory for the launcher process.
        timeout : float, optional
            Maximum launcher execution time in seconds.

        Returns
        -------
        subprocess.CompletedProcess[str]
            Launcher result with successful editor children detached.

        Raises
        ------
        ProcessError
            If adapting or executing the launcher fails.
        Cancelled
            If the user interrupts the launcher.
        """
        argv, overrides = editor_command(launcher, project)
        return self._run(argv, cwd=cwd, timeout=timeout, env=overrides, keep_children=True)

    def _run( # NOSONAR
        self,
        argv: Sequence[str | Path],
        *,
        cwd: Path,
        timeout: float,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        keep_children: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """
        Execute the child lifecycle with explicit descendant retention.

        Parameters
        ----------
        argv : Sequence[str or Path]
            Separate native process arguments.
        cwd : Path
            Explicit existing working directory.
        timeout : float
            Maximum execution time in seconds.
        env : Mapping[str, str] or None, optional
            Trusted environment overrides.
        check : bool, optional
            Reject unsuccessful child exit codes.
        keep_children : bool, optional
            Retain detached editor children after launcher completion.

        Returns
        -------
        subprocess.CompletedProcess[str]
            Captured child result for internal use.

        Raises
        ------
        ProcessError
            If the process cannot complete within its controlled lifetime.
        Cancelled
            If the user interrupts process setup or execution.
        ValueError
            If the supplied arguments, directory or timeout are invalid.
        """
        if isinstance(argv, (str, bytes)) or not argv:
            raise ValueError(MESSAGES["process_arguments_invalid"])
        arguments = [str(argument) for argument in argv]
        if any("\x00" in argument for argument in arguments):
            raise ValueError(MESSAGES["process_arguments_null"])
        executable = Path(arguments[0])
        if not executable.is_absolute() or executable.suffix.lower() in {".cmd", ".bat"}:
            raise ProcessError(MESSAGES["process_executable_invalid"])
        working_directory = Path(cwd).resolve(strict=True)
        if not working_directory.is_dir() or timeout <= 0:
            raise ValueError(MESSAGES["process_working_directory_invalid"])
        environment = isolated_environment(
            self.environ, cwd=working_directory, excluded_roots=(working_directory,)
        )
        if env:
            # Explicit overrides are controlled by the caller, never inherited.
            environment.update(env)
        process: subprocess.Popen[str] | None = None
        job: _WindowsJob | None = None
        try:
            with _defer_spawn_interrupt():
                try:
                    process = subprocess.Popen(
                        arguments,
                        cwd=str(working_directory),
                        env=environment,
                        shell=False,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        start_new_session=os.name != "nt",
                        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        | 0x00000004
                        if os.name == "nt"
                        else 0,
                    )
                except OSError:
                    raise ProcessError(MESSAGES["process_start_failed"]) from None
                if os.name == "nt":
                    try:
                        job = _WindowsJob(process)
                        _resume_windows_process(process)
                    except OSError:
                        self._stop(process, job)
                        raise ProcessError(MESSAGES["process_group_failed"]) from None
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._stop(process, job)
                raise ProcessError(MESSAGES["process_timeout"].format(timeout=timeout)) from None
            result = subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
            if check and result.returncode:
                raise ProcessError(MESSAGES["process_exit_failed"].format(code=result.returncode))
            return result
        except KeyboardInterrupt:
            if process is not None:
                self._stop(process, job)
            raise Cancelled(MESSAGES["process_cancelled"]) from None
        finally:
            if job is not None:
                job.close(terminate=not keep_children)

    @staticmethod
    def _stop(process: subprocess.Popen[str], job: _WindowsJob | None) -> None:
        """
        Terminate owned children and wait for their controlled leader.

        Parameters
        ----------
        process : subprocess.Popen[str]
            Child process created by this runner.
        job : _WindowsJob or None
            Windows descendant ownership handle, when available.
        """
        if os.name == "nt":
            if job is not None:
                job.close()
            elif process.poll() is None:
                # Absolute OS executable, own live PID only, never an image name.
                system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
                taskkill = Path(system_root) / "System32" / "taskkill.exe"
                try:
                    subprocess.run(
                        [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                        cwd=system_root,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        shell=False,
                        timeout=10,
                        check=False,
                    )
                except OSError, subprocess.TimeoutExpired:
                    process.kill()
        else:
            kill_group = getattr(os, "killpg", None)
            if kill_group is None:
                process.kill()
                process.wait(timeout=5)
                return
            with suppress(ProcessLookupError):
                kill_group(process.pid, signal.SIGTERM)
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2)
            # The leader may have exited while descendants still own pipes.
            with suppress(ProcessLookupError):
                kill_group(process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
