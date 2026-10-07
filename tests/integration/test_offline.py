"""Test local Git cloning with explicitly simulated uv and framework responses.

The production installer has no alternate-source flag. The test-only Runner seam
replaces the official URL with a disposable local repository, retaining all flags.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import tomlkit
from packaging.requirements import Requirement

from orionis_installer.configuration import read_env
from orionis_installer.exceptions import Cancelled, InstallerError, ProcessError
from orionis_installer.installer import CONFIG_PROBE, METADATA_PROBE, Installer
from orionis_installer.models import SKELETON_URL, Database, InstallationPlan, State, Storage
from orionis_installer.prerequisites import Prerequisites
from orionis_installer.processes import Runner, isolated_environment, resolve_executable

FIXTURE = Path(__file__).parents[1] / "fixtures" / "skeleton"


@pytest.fixture
def git(tmp_path):
    """
    Resolve Git outside the disposable fixture directory.

    Parameters
    ----------
    tmp_path : Path
        Directory excluded from executable resolution.

    Returns
    -------
    Path
        Trusted native Git executable, or skip the test when unavailable.
    """
    executable = resolve_executable("git", tmp_path)
    if executable is None:
        pytest.skip("Git is unavailable for offline integration tests.")
    return executable


@pytest.fixture
def repository_factory(tmp_path, git):
    """
    Provide a factory for disposable local skeleton repositories.

    Parameters
    ----------
    tmp_path : Path
        Parent directory for fixture repositories.
    git : Path
        Trusted Git executable.

    Returns
    -------
    Callable
        Factory returning a repository path and its committed revision.
    """

    def create(branch="master"):
        """
        Create and commit an offline skeleton repository on the requested branch.

        Parameters
        ----------
        branch : str, optional
            Initial Git branch used to exercise branch validation.

        Returns
        -------
        tuple[Path, str]
            Disposable repository and its revision.
        """
        repository = tmp_path / f"offline fixture repository {branch}"
        shutil.copytree(FIXTURE, repository)
        runner = Runner()
        runner.run([git, "init", "-b", branch], cwd=repository)
        runner.run([git, "add", "-A"], cwd=repository)
        runner.run(
            [
                git,
                "-c",
                "user.name=Offline Test Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-m",
                "Commit disposable offline skeleton fixture",
            ],
            cwd=repository,
        )
        revision = runner.run([git, "rev-parse", "HEAD"], cwd=repository).stdout.strip()
        return repository, revision

    return create


class LocalFixtureRunner(Runner):
    """Replace only the official clone URL and simulate application commands."""

    def __init__(self, repository, plan, *, sync_fails=False, cancel=None, environ=None):
        """
        Initialize local source substitution and controlled failure settings.

        Parameters
        ----------
        repository : Path
            Disposable Git repository substituted for the official URL.
        plan : InstallationPlan
            Plan whose destination and driver choices must be respected.
        sync_fails : bool, optional
            Whether to inject a synchronization failure after publication.
        cancel : str or None, optional
            Clone or synchronization phase at which to cancel.
        environ : dict[str, str] or None, optional
            Inherited environment used to test child-process isolation.
        """
        super().__init__(environ=environ)
        self.repository = repository
        self.plan = plan
        self.sync_fails = sync_fails
        self.cancel = cancel
        self.calls = []
        self.clone_calls = []

    def run(self, argv, *, cwd, timeout=300, env=None, check=True):
        """
        Run real Git commands and simulate uv and framework responses.

        Parameters
        ----------
        argv : Sequence[str or Path]
            Command arguments intercepted for fixture operations.
        cwd : Path
            Explicit working directory checked against the plan.
        timeout : float, optional
            Time limit passed to real child processes.
        env : dict[str, str] or None, optional
            Additional environment for real commands.
        check : bool, optional
            Whether real nonzero exits raise a process error.

        Returns
        -------
        subprocess.CompletedProcess
            Real Git result or a clearly simulated application result.
        """
        arguments = [str(argument) for argument in argv]
        self.calls.append((arguments, Path(cwd)))
        if arguments[1] == "clone":
            self.clone_calls.append(arguments.copy())
            assert arguments.count(SKELETON_URL) == 1
            assert arguments[arguments.index("--branch") + 1] == "master"
            assert "--no-recurse-submodules" in arguments
            assert not (Path(arguments[-1]) / ".venv").exists()
            if self.cancel == "clone":
                raise Cancelled("offline fixture cancellation")
            arguments[arguments.index(SKELETON_URL)] = str(self.repository)
            return super().run(arguments, cwd=cwd, timeout=timeout, env=env, check=check)
        if arguments[1] == "sync":
            assert Path(cwd) == self.plan.path
            assert not (Path(cwd) / ".venv").exists()
            assert not (Path(cwd) / ".git").exists()
            assert not list(Path(cwd).parent.glob(".*orionis-*/.venv"))
            controlled = isolated_environment(self.environ, cwd=Path(cwd))
            for variable in [
                "UV_PROJECT_ENVIRONMENT",
                "VIRTUAL_ENV",
                "PYTHONHOME",
                "PYTHONPATH",
                "UV_PROJECT",
                "UV_ENV_FILE",
                "APP_KEY",
            ]:
                assert variable not in controlled
            if self.cancel == "sync":
                raise Cancelled("offline fixture cancellation")
            if self.sync_fails:
                (Path(cwd) / "user-created-during-sync.txt").write_text("preserve user data")
                raise ProcessError("Injected offline uv synchronization failure")
            python = (
                Path(cwd) / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )
            python.parent.mkdir(parents=True)
            python.write_text("Dummy interpreter; simulated probes never execute this file.")
            (Path(cwd) / "uv.lock").write_text("# Simulated offline lockfile\nversion = 1\n")
        elif METADATA_PROBE in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                json.dumps(
                    {
                        "python": "3.14.6",
                        "prefix": str(self.plan.path / ".venv"),
                        "version": "0.801.0",
                        "extras": ["factories", "s3", "redshift"],
                        "requirements": [
                            "faker>=37; extra == 'factories'",
                            "boto3>=1; extra == 's3'",
                            "redshift-connector>=2; extra == 'redshift'",
                        ],
                        "installed": {
                            "faker": "37.0.0",
                            "boto3": "1.99.0",
                            "redshift-connector": "2.99.0",
                        },
                    }
                ),
                "",
            )
        elif CONFIG_PROBE in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                json.dumps(
                    {
                        "database": self.plan.active_database.value,
                        "driver": self.plan.active_database.value,
                        "storage": self.plan.active_storage.value,
                        "storage_driver": self.plan.active_storage.value,
                        "name": self.plan.name,
                    }
                ),
                "",
            )
        elif "key:generate" in arguments:
            with (Path(cwd) / ".env").open("a", encoding="utf-8") as environment:
                environment.write("\nAPP_KEY='offline-fixture-key'\n")
        elif "reactor" not in arguments and "-c" not in arguments:
            return super().run(arguments, cwd=cwd, timeout=timeout, env=env, check=check)
        return subprocess.CompletedProcess(arguments, 0, "", "")


def prerequisites(git):
    """
    Build prerequisite metadata for simulated uv and Python commands.

    Parameters
    ----------
    git : Path
        Trusted executable used for real local cloning.

    Returns
    -------
    Prerequisites
        Test-only tools whose Python and uv responses are intercepted.
    """
    return Prerequisites(Path(sys.executable), git, Path(sys.executable), "3.14.6")


@pytest.mark.parametrize(
    "storage,database", [(Storage.LOCAL, Database.SQLITE), (Storage.S3, Database.REDSHIFT)]
)
def test_offline_local_git_download_and_configured_final_environment(
    tmp_path, git, repository_factory, storage, database
):
    """
    Verify local cloning, extras and environment creation at the final location.

    Parameters
    ----------
    tmp_path : Path
        Parent directory for the destination.
    git : Path
        Trusted Git executable.
    repository_factory : Callable
        Factory for committed skeleton fixtures.
    storage : Storage
        Storage choice to configure.
    database : Database
        Database choice to configure.
    """
    repository, revision = repository_factory()
    plan = InstallationPlan(
        "offline-app", tmp_path / "application with spaces ñ &", storage=storage, database=database
    )
    runner = LocalFixtureRunner(repository, plan)
    result = Installer(plan, prerequisites(git), runner).install()
    assert result.creation == State.COMPLETED
    assert result.framework_version == "0.801.0"
    assert len(runner.clone_calls) == 1
    assert len([call for call in runner.calls if call[0][1] == "sync"]) == 1
    assert (plan.path / ".venv").is_dir() and (plan.path / "uv.lock").exists()
    assert not (plan.path / ".git").exists()
    assert (plan.path / ".gitignore").exists()
    provenance = json.loads((plan.path / ".orionis-install.json").read_text())
    assert provenance["sha"] == revision and provenance["branch"] == "master"
    document = tomlkit.parse((plan.path / "pyproject.toml").read_text())
    framework = Requirement(document["project"]["dependencies"][0])
    assert framework.extras == set(plan.extras)
    assert document["tool"]["fixture"]["keep"] == "application-specific-value"
    assert read_env(plan.path / ".env")["APP_KEY"] == "offline-fixture-key"
    assert not list(tmp_path.glob(".*orionis*"))


def test_missing_master_branch_never_falls_back_or_publishes(tmp_path, git, repository_factory):
    """
    Reject a fixture without master before publishing any destination.

    Parameters
    ----------
    tmp_path : Path
        Parent directory for the rejected destination.
    git : Path
        Trusted Git executable.
    repository_factory : Callable
        Factory used to create a repository with only main.
    """
    repository, _revision = repository_factory("main")
    plan = InstallationPlan("app", tmp_path / "app")
    runner = LocalFixtureRunner(repository, plan)
    with pytest.raises(ProcessError):
        Installer(plan, prerequisites(git), runner).install()
    assert len(runner.clone_calls) == 1
    assert not plan.path.exists() and not list(tmp_path.glob(".*orionis*"))


def test_real_git_clone_failure_preserves_unrelated_files(tmp_path, git):
    """
    Preserve unrelated files when a real local clone fails.

    Parameters
    ----------
    tmp_path : Path
        Directory containing the destination and unrelated file.
    git : Path
        Trusted Git executable.
    """
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("preserve")
    plan = InstallationPlan("app", tmp_path / "app")
    with pytest.raises(ProcessError):
        Installer(
            plan, prerequisites(git), LocalFixtureRunner(tmp_path / "absent-repository", plan)
        ).install()
    assert unrelated.read_text() == "preserve"
    assert not plan.path.exists()


def test_uv_failure_after_real_local_clone_preserves_user_file_and_recovery(
    tmp_path, git, repository_factory
):
    """
    Preserve the published project and user file after synchronization fails.

    Parameters
    ----------
    tmp_path : Path
        Parent directory for the application.
    git : Path
        Trusted Git executable.
    repository_factory : Callable
        Factory for a clonable skeleton fixture.
    """
    repository, _revision = repository_factory()
    plan = InstallationPlan("app", tmp_path / "app")
    with pytest.raises(InstallerError, match="uv sync --python 3.14"):
        Installer(
            plan, prerequisites(git), LocalFixtureRunner(repository, plan, sync_fails=True)
        ).install()
    assert (plan.path / "user-created-during-sync.txt").read_text() == "preserve user data"
    assert (plan.path / ".env").exists() and not (plan.path / ".git").exists()


def test_uvx_and_foreign_project_environment_cannot_receive_application_venv(
    tmp_path, git, repository_factory
):
    """
    Prevent inherited uv and Python settings from redirecting the environment.

    Parameters
    ----------
    tmp_path : Path
        Parent directory for the application and foreign environment.
    git : Path
        Trusted Git executable.
    repository_factory : Callable
        Factory for a clonable skeleton fixture.
    """
    repository, _revision = repository_factory()
    foreign = tmp_path / "foreign environment"
    plan = InstallationPlan("app", tmp_path / "final project")
    inherited = dict(os.environ)
    inherited.update(
        {
            "UV_PROJECT_ENVIRONMENT": str(foreign),
            "VIRTUAL_ENV": str(foreign),
            "UV_PROJECT": str(foreign),
            "PYTHONHOME": str(foreign),
            "PYTHONPATH": str(foreign),
            "UV_ENV_FILE": str(foreign / ".env"),
            "APP_KEY": "private-foreign-key",
        }
    )
    runner = LocalFixtureRunner(repository, plan, environ=inherited)
    result = Installer(plan, prerequisites(git), runner).install()
    assert result.creation == State.COMPLETED
    assert (plan.path / ".venv").is_dir() and not foreign.exists()
    assert read_env(plan.path / ".env")["APP_KEY"] == "offline-fixture-key"


@pytest.mark.parametrize("phase", ["clone", "sync"])
def test_offline_cancellation_respects_publication_boundary(
    tmp_path, git, repository_factory, phase
):
    """
    Preserve only published content when cancellation interrupts installation.

    Parameters
    ----------
    tmp_path : Path
        Parent directory inspected for leftover staging files.
    git : Path
        Trusted Git executable.
    repository_factory : Callable
        Factory for a clonable skeleton fixture.
    phase : str
        Clone or synchronization phase at which to cancel.
    """
    repository, _revision = repository_factory()
    plan = InstallationPlan("app", tmp_path / "app")
    with pytest.raises(Cancelled):
        Installer(
            plan, prerequisites(git), LocalFixtureRunner(repository, plan, cancel=phase)
        ).install()
    assert plan.path.exists() == (phase == "sync")
    assert not list(tmp_path.glob(".*orionis*"))
