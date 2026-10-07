"""Regressions found while reviewing the real framework's effective contracts."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from orionis_installer.exceptions import CompatibilityError, PrerequisiteError, ProcessError
from orionis_installer.installer import ensure_app_key, verify_configuration
from orionis_installer.models import (
    Database,
    InstallationPlan,
    InstallationResult,
    PostInstallOptions,
    State,
    Storage,
)
from orionis_installer.post_install import connection_ready, run_post_install, seeder_safety
from orionis_installer.prerequisites import Prerequisites, check_prerequisites
from orionis_installer.processes import Runner, resolve_executable


@pytest.mark.parametrize(
    "source",
    [
        "await User.create({'password': await Hash.make('static-example')})",
        "await User.create(dict(password=await Hash.make('static-example')))",
        "await User.create({'pass' + 'word': await Hash.make('static-example')})",
        "user.password = await Hash.make('static-example')",
        "await credential_factory.create_administrator()",
    ],
)
@pytest.mark.parametrize("nested", [False, True])
def test_unknown_administrative_seeder_forms_are_rejected_before_execution(
    tmp_path, source, nested
):
    """Verify that unknown administrative seeder forms are rejected before execution.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    source : str
        Synthetic seeder source submitted to the syntax-based safety guard.
    nested : bool
        Whether to place the seeder below a nested discovery directory.
    """
    directory = tmp_path / "database" / "seeders"
    if nested:
        directory /= "nested"
    directory.mkdir(parents=True)
    (directory / "s0001.py").write_text("async def run():\n    " + source + "\n", encoding="utf-8")
    with pytest.raises(CompatibilityError) as raised:
        seeder_safety(tmp_path)
    assert "static-example" not in str(raised.value)


@pytest.mark.parametrize("source", ["", "# empty fixture\n", '"""Inert fixture."""\n', "pass\n"])
def test_inert_or_empty_offline_seeders_are_safe(tmp_path, source):
    """Verify that inert or empty offline seeders are safe.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    source : str
        Synthetic seeder source submitted to the syntax-based safety guard.
    """
    directory = tmp_path / "database" / "seeders"
    directory.mkdir(parents=True)
    (directory / "s0001.py").write_text(source, encoding="utf-8")
    seeder_safety(tmp_path)


def test_effective_cloud_driver_must_match_the_selected_disk(tmp_path):
    """Verify rejection of an effective cloud driver that differs from the selected disk.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    plan = InstallationPlan("blog", tmp_path / "application", storage=Storage.GCS)
    with pytest.raises(CompatibilityError):
        verify_configuration(
            {
                "database": "sqlite",
                "driver": "sqlite",
                "storage": "gcs",
                "storage_driver": "s3",
                "name": "blog",
            },
            plan,
        )


def test_all_cloud_drivers_still_validate_the_actual_selected_driver(tmp_path):
    """Verify the effective cloud driver when all storage SDKs are selected.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    plan = InstallationPlan(
        "blog", tmp_path / "application", storage=Storage.ALL, default_storage=Storage.AZURE
    )
    with pytest.raises(CompatibilityError):
        verify_configuration(
            {
                "database": "sqlite",
                "driver": "sqlite",
                "storage": "azure",
                "storage_driver": "gcs",
                "name": "blog",
            },
            plan,
        )


def test_seeders_directory_cannot_redirect_to_unreviewed_external_code(tmp_path):
    """Verify rejection of a seeder directory that redirects to external code.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    database = tmp_path / "database"
    database.mkdir()
    external = tmp_path / "external-seeders"
    external.mkdir()
    (external / "s0001.py").write_text("await User.create(dict(password='static-example'))\n")
    pointer = database / "seeders"
    try:
        pointer.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"OS does not permit disposable symlinks: {exc}")
    try:
        with pytest.raises(CompatibilityError):
            seeder_safety(tmp_path)
        assert (external / "s0001.py").is_file()
    finally:
        pointer.unlink()


@pytest.mark.parametrize(
    "port", ["²", "١٢٣", "9" * 4301, "", "0", "65536", "-1", "1.0", "+80", " 80"]
)
def test_invalid_external_ports_fail_readiness_without_conversion_exceptions(port):
    """Verify that malformed or oversized ports fail readiness without conversion errors.

    Parameters
    ----------
    port : str
        Database port text exercised by the parameterized case.
    """
    assert not connection_ready(
        {
            "DB_CONNECTION": "mysql",
            "DB_HOST": "localhost",
            "DB_PORT": port,
            "DB_DATABASE": "offline-fixture",
            "DB_USERNAME": "offline-user",
            "DB_PASSWORD": "offline-password",
        }
    )


@pytest.mark.parametrize("port", ["1", "3306", "65535"])
def test_external_port_bounds_accept_ascii_decimal_values(port):
    """Verify acceptance of valid ASCII decimal ports, including both bounds.

    Parameters
    ----------
    port : str
        Database port text exercised by the parameterized case.
    """
    assert connection_ready(
        {
            "DB_CONNECTION": "mysql",
            "DB_HOST": "localhost",
            "DB_PORT": port,
            "DB_DATABASE": "offline-fixture",
            "DB_USERNAME": "offline-user",
            "DB_PASSWORD": "offline-password",
        }
    )


def test_invalid_port_during_credential_prompt_still_attempts_independent_editor(
    tmp_path, monkeypatch
):
    """Verify that an invalid prompted port preserves the independent editor attempt.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    monkeypatch : pytest.MonkeyPatch
        Fixture replacing process state or external dependencies for the test.
    """
    root = tmp_path / "application"
    root.mkdir()
    (root / ".env").write_text(
        'DB_CONNECTION="mysql"\nDB_HOST="localhost"\nDB_PORT=3306\n'
        'DB_DATABASE="fixture"\nDB_USERNAME="configure-me"\n',
        encoding="utf-8",
    )
    plan = InstallationPlan("blog", root, database=Database.MYSQL)
    result = InstallationResult(plan, creation=State.COMPLETED, published=True)

    class OfflineUI:
        """Supply offline connection answers without displaying or requesting input."""

        answers = iter(["localhost", "²"])

        def confirm(self, label, default):
            """Accept the offline connection configuration request.

            Parameters
            ----------
            label : str
                Prompt or field label supplied by the post-install operation.
            default : bool
                Fallback answer when no predetermined confirmation remains.

            Returns
            -------
            bool
                Predetermined confirmation or the supplied fallback answer.
            """
            return True

        def text(self, label, default="", *, validator=None, password=False):
            """Return the next predetermined connection answer without prompting.

            Parameters
            ----------
            label : str
                Prompt or field label supplied by the post-install operation.
            default : str, optional
                Suggested text accepted to match the UI protocol.
            validator : Callable or None, optional
                Optional validation callback accepted by the UI protocol.
            password : bool, optional
                Whether the requested input must be treated as a protected password.

            Returns
            -------
            str
                Next predetermined connection field value.
            """
            return next(self.answers)

        def message(self, value):
            """Accept a progress message without producing UI output.

            Parameters
            ----------
            value : str
                Post-install output accepted or recorded by the UI double.
            """
            pass

        def warning(self, value):
            """Accept a diagnostic warning without producing UI output.

            Parameters
            ----------
            value : str
                Post-install output accepted or recorded by the UI double.
            """
            pass

    class OfflineRunner:
        """Record editor launches and forbid real migration processes."""

        editor_calls = []

        def run(self, *args, **kwargs):
            """Fail the test if incomplete credentials reach process execution.

            Parameters
            ----------
            *args : object
                Positional process arguments accepted by the deliberately non-executing double.
            **kwargs : object
                Keyword process settings accepted by the simulated runner.
            """
            pytest.fail("No process should run with incomplete migration configuration")

        def open_editor(self, launcher, project, **kwargs):
            """Record the trusted editor launch without starting an editor.

            Parameters
            ----------
            launcher : Path
                Trusted editor executable supplied to the simulated launch.
            project : Path
                Final application directory supplied to the simulated editor launch.
            **kwargs : object
                Keyword process settings accepted by the simulated runner.
            """
            self.editor_calls.append((launcher, project))

    launcher = tmp_path / "trusted-native-code"
    monkeypatch.setattr(
        "orionis_installer.post_install.resolve_executable", lambda *a, **k: launcher
    )
    runner = OfflineRunner()
    prerequisites = Prerequisites(tmp_path / "uv", tmp_path / "git", tmp_path / "python", "3.14.6")
    run_post_install(
        result,
        PostInstallOptions(git=False, migrate=True, open=True),
        prerequisites,
        runner,
        OfflineUI(),
        no_interaction=False,
    )
    assert result.migrations == State.FAILED
    assert result.editor == State.COMPLETED
    assert result.exit_code == 3
    assert runner.editor_calls == [(launcher, root)]


def test_custom_uv_configuration_is_not_silently_discarded(tmp_path, monkeypatch):
    """Verify explicit rejection of custom uv configuration before any tool executes.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    monkeypatch : pytest.MonkeyPatch
        Fixture replacing process state or external dependencies for the test.
    """
    configuration = tmp_path / "private-uv.toml"
    original = 'python-downloads = "never"\n'
    configuration.write_text(original, encoding="utf-8")
    runner = Runner(environ={"UV_CONFIG_FILE": str(configuration), "PATH": ""})
    monkeypatch.setattr(
        runner, "run", lambda *a, **k: pytest.fail("No tool should run before policy validation")
    )
    with pytest.raises(PrerequisiteError, match="UV_CONFIG_FILE"):
        check_prerequisites(runner, cwd=tmp_path)
    assert configuration.read_text(encoding="utf-8") == original


def test_standard_tool_installation_below_home_is_available_from_home(tmp_path):
    """Verify trusted tool discovery below HOME when invoked from HOME.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    home = tmp_path / "home"
    directory = home / ".local" / "bin"
    directory.mkdir(parents=True)
    executable = directory / ("uv.exe" if os.name == "nt" else "uv")
    executable.write_bytes(b"offline native executable lookup fixture")
    executable.chmod(0o700)
    assert resolve_executable("uv", home, path=str(directory)) == executable.resolve()


@pytest.mark.parametrize("managed", [True, False])
def test_prerequisite_lookup_from_home_accepts_managed_python_but_rejects_working_venv(
    tmp_path, managed
):
    """Verify HOME-based tool discovery accepts managed Python and rejects the working .venv.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    managed : bool
        Whether the discovered Python belongs to uv management or a working .venv.
    """
    home = tmp_path / "home"
    user_bin = home / ".local" / "bin"
    system_bin = tmp_path / "system-bin"
    user_bin.mkdir(parents=True)
    system_bin.mkdir()
    uv = user_bin / ("uv.exe" if os.name == "nt" else "uv")
    git = system_bin / ("git.exe" if os.name == "nt" else "git")
    if managed:
        python = (
            home / "AppData" / "Roaming" / "uv" / "python" / "cpython-3.14" / "python.exe"
            if os.name == "nt"
            else home / ".local" / "share" / "uv" / "python" / "cpython-3.14" / "bin" / "python"
        )
    else:
        python = home / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    for executable in (uv, git, python):
        executable.write_bytes(b"offline prerequisite executable fixture")
        executable.chmod(0o700)

    class OfflinePrerequisiteRunner:
        """Simulate trusted tool versions and Python discovery without executing binaries."""

        environ = {"PATH": os.pathsep.join((str(user_bin), str(system_bin))), "HOME": str(home)}

        def run(self, argv, **kwargs):
            """Return deterministic version and interpreter discovery results.

            Parameters
            ----------
            argv : Sequence[str | Path]
                Command arguments recorded or inspected by the process double.
            **kwargs : object
                Keyword process settings accepted by the simulated runner.

            Returns
            -------
            SimpleNamespace
                Synthetic process result containing the expected captured output.
            """
            if "find" in argv:
                return SimpleNamespace(returncode=0, stdout=str(python) + "\n")
            if Path(argv[0]) == python:
                return SimpleNamespace(returncode=0, stdout=json.dumps([3, 14, 6, "final"]))
            return SimpleNamespace(returncode=0, stdout="offline version probe\n")

    if managed:
        result = check_prerequisites(OfflinePrerequisiteRunner(), cwd=home)
        assert result.uv == uv.resolve()
        assert result.python == python.resolve()
        assert result.python_version == "3.14.6"
    else:
        with pytest.raises(PrerequisiteError):
            check_prerequisites(OfflinePrerequisiteRunner(), cwd=home)


@pytest.mark.parametrize("before", [None, "", "null", "None", "nil", "nan", "NULL", " null "])
def test_unprefixed_null_app_key_uses_real_generation_contract_without_force(tmp_path, before):
    """Verify key generation for unprefixed null values without forcing replacement.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    before : str or None
        Raw missing or null-like APP_KEY value supplied before bootstrap.
    """
    path = tmp_path / ".env"
    path.write_text("" if before is None else f"APP_KEY='{before}'\n", encoding="utf-8")

    class BootstrapFixture:
        """Simulate the application bootstrap used to verify key generation behavior."""

        calls = []

        def run(self, argv, **kwargs):
            """Record the bootstrap command and persist a synthetic generated key.

            Parameters
            ----------
            argv : Sequence[str | Path]
                Command arguments recorded or inspected by the process double.
            **kwargs : object
                Keyword process settings accepted by the simulated runner.

            Returns
            -------
            SimpleNamespace
                Synthetic successful bootstrap process output.
            """
            self.calls.append(argv)
            # A deterministic synthetic key; no real application or secrets are used.
            path.write_text("APP_KEY='generated-key-fixture'\n", encoding="utf-8")
            return SimpleNamespace(stdout="")

    runner = BootstrapFixture()
    ensure_app_key(runner, tmp_path / "python", tmp_path)
    assert runner.calls == [[tmp_path / "python", "-B", "reactor", "key:generate"]]
    assert "--force" not in runner.calls[0]


def test_bootstrap_leaving_a_null_key_fails_verification(tmp_path):
    """Verify failure when bootstrap leaves a null application key in the environment.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    path = tmp_path / ".env"
    path.write_text("APP_KEY=null\n", encoding="utf-8")
    runner = SimpleNamespace(run=lambda *a, **k: SimpleNamespace(stdout=""))
    with pytest.raises(CompatibilityError):
        ensure_app_key(runner, tmp_path / "python", tmp_path)


def test_explicit_str_null_is_an_existing_invalid_key_and_is_never_regenerated(tmp_path):
    """Verify that an explicitly typed invalid key is preserved and never regenerated.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    path = tmp_path / ".env"
    original = "APP_KEY='str:null'\n"
    path.write_text(original, encoding="utf-8")

    class BootstrapFixture:
        """Simulate the application bootstrap used to verify key generation behavior."""

        def run(self, argv, **kwargs):
            """Reject an invalid existing key without invoking key regeneration.

            Parameters
            ----------
            argv : Sequence[str | Path]
                Command arguments recorded or inspected by the process double.
            **kwargs : object
                Keyword process settings accepted by the simulated runner.

            Raises
            ------
            ProcessError
                Always, to model rejection of the existing invalid application key.
            """
            assert "key:generate" not in argv
            assert "--force" not in argv
            raise ProcessError("The actual bootstrap rejects an invalid existing key")

    with pytest.raises(ProcessError):
        ensure_app_key(BootstrapFixture(), tmp_path / "python", tmp_path)
    assert path.read_text(encoding="utf-8") == original
