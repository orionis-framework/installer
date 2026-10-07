"""Verify installation lifecycles with explicit offline framework metadata."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.requirements import Requirement

from orionis_installer import installer
from orionis_installer.configuration import read_env
from orionis_installer.exceptions import Cancelled, CompatibilityError, InstallerError, ProcessError
from orionis_installer.installer import (
    Installer,
    ensure_app_key,
    verify_configuration,
    verify_metadata,
)
from orionis_installer.messages import MESSAGES
from orionis_installer.models import Database, InstallationPlan, State, Storage
from orionis_installer.prerequisites import Prerequisites

FIXTURE = Path(__file__).parents[1] / "fixtures" / "skeleton"
ALL_EXTRAS = [
    "factories",
    "s3",
    "azure",
    "gcs",
    "storage",
    "mysql",
    "pgsql",
    "oracle",
    "sqlserver",
    "redshift",
    "database",
]
DRIVER_PACKAGES = {
    "s3": "boto3",
    "azure": "azure-storage-blob",
    "gcs": "google-cloud-storage",
    "mysql": "pymysql",
    "pgsql": "psycopg",
    "oracle": "oracledb",
    "sqlserver": "pyodbc",
    "redshift": "redshift-connector",
}


def fixture_metadata(plan: InstallationPlan) -> dict:
    """Build explicit framework metadata for an offline application fixture.

    Parameters
    ----------
    plan : InstallationPlan
        Validated choices and final destination for the application fixture.

    Returns
    -------
    dict
        Explicit simulated framework metadata; no installed distribution is queried.
    """
    requirements = ["rich>=15", "faker>=37; extra == 'factories'"]
    for extra, package in DRIVER_PACKAGES.items():
        aggregate = "storage" if extra in {"s3", "azure", "gcs"} else "database"
        requirements.append(f"{package}>=1; extra == '{extra}' or extra == '{aggregate}'")
    return {
        "python": "3.14.6",
        "prefix": str(plan.path / ".venv"),
        "version": "0.801.0",
        "extras": ALL_EXTRAS.copy(),
        "requirements": requirements,
        "installed": {
            "rich": "15.0.0",
            "faker": "37.0.0",
            **dict.fromkeys(DRIVER_PACKAGES.values(), "99.0.0"),
        },
    }


class FixtureInstallerRunner:
    """Simulate uv and framework responses for installation lifecycle tests."""

    def __init__(self, plan, *, fail_phase=None, cancellation=None):
        """Initialize controlled installation responses and failure injection.

        Parameters
        ----------
        plan : InstallationPlan
            Validated choices and final destination for the application fixture.
        fail_phase : str or None, optional
            Installation phase selected for injected failure.
        cancellation : str or None, optional
            Installation phase selected for injected cancellation.
        """
        self.plan = plan
        self.fail_phase = fail_phase
        self.cancellation = cancellation
        self.calls = []
        self.data = fixture_metadata(plan)

    def run(self, argv, *, cwd, timeout=300, **kwargs):
        """Simulate installation commands while checking the publication boundary.

        Parameters
        ----------
        argv : Sequence[str or Path]
            Separate command arguments supplied to the process fixture.
        cwd : Path
            Explicit working directory recorded by the process fixture.
        timeout : float, optional
            Execution limit recorded by the process fixture.
        **kwargs : dict
            Additional options accepted by the controlled fixture.

        Returns
        -------
        SimpleNamespace
            Controlled framework metadata, configuration or empty command output.

        Raises
        ------
        Cancelled
            If the selected installation phase should be cancelled.
        ProcessError
            If the selected installation phase should fail.
        """
        arguments = [str(argument) for argument in argv]
        self.calls.append((arguments, Path(cwd), timeout))
        phase = (
            "sync"
            if arguments[1] == "sync"
            else (
                "metadata"
                if installer.METADATA_PROBE in arguments
                else ("configuration" if installer.CONFIG_PROBE in arguments else "key")
            )
        )
        if self.cancellation == phase:
            raise Cancelled("fixture cancellation")
        if self.fail_phase == phase:
            if phase == "sync":
                (Path(cwd) / "user-during-install.txt").write_text("keep this user file")
            raise ProcessError("fixture operation failed")
        if phase == "sync":
            assert Path(cwd) == self.plan.path
            assert not (Path(cwd) / ".venv").exists()
            assert not any(
                path.name == ".venv" for path in Path(cwd).parent.glob(".*orionis-*/.venv")
            )
            python = (
                Path(cwd) / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )
            python.parent.mkdir(parents=True)
            python.write_text("offline fixture interpreter; never executed")
            (Path(cwd) / "uv.lock").write_text("# Offline lockfile fixture\nversion = 1\n")
        elif phase == "metadata":
            return SimpleNamespace(stdout=json.dumps(self.data))
        elif phase == "configuration":
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "database": self.plan.active_database.value,
                        "driver": self.plan.active_database.value,
                        "storage": self.plan.active_storage.value,
                        "storage_driver": self.plan.active_storage.value,
                        "name": self.plan.name,
                    }
                )
            )
        elif "key:generate" in arguments:
            with (Path(cwd) / ".env").open("a", encoding="utf-8") as environment:
                environment.write("\nAPP_KEY='fixture-generated-key'\n")
        return SimpleNamespace(stdout="")


@pytest.fixture
def tools():
    """Provide executable prerequisites for the simulated installer.

    Returns
    -------
    Prerequisites
        Executable paths used only through simulated installation commands.
    """
    return Prerequisites(Path(sys.executable), Path(sys.executable), Path(sys.executable), "3.14.6")


@pytest.fixture
def clone_fixture(monkeypatch):
    """Replace remote cloning with the identified offline skeleton.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Scoped replacement fixture for controlled dependencies.
    """

    def clone(staging, git, runner):
        """Copy the offline skeleton into exclusively owned staging.

        Parameters
        ----------
        staging : Path
            Owned staging directory used by the clone fixture.
        git : Path
            Trusted Git executable supplied to the clone fixture.
        runner : object
            Process fixture supplied to the clone seam.

        Returns
        -------
        str
            Explicit fixture revision recorded as installation provenance.
        """
        shutil.copytree(FIXTURE, staging, dirs_exist_ok=True)
        assert not (staging / ".venv").exists()
        return "a" * 40

    monkeypatch.setattr(installer, "clone_skeleton", clone)


@pytest.mark.parametrize(
    "storage,database",
    [
        (Storage.LOCAL, Database.SQLITE),
        (Storage.S3, Database.PGSQL),
        (Storage.AZURE, Database.MYSQL),
        (Storage.S3, Database.REDSHIFT),
        (Storage.ALL, Database.ALL),
    ],
)
def test_install_configures_extras_before_single_sync_at_final_location(
    tmp_path, tools, clone_fixture, storage, database
):
    """Verify that extras are configured before one sync in the final directory.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    tools : Prerequisites
        Executable paths used by the simulated installer.
    clone_fixture : None
        Fixture that replaces remote cloning with the offline template.
    storage : Storage
        Storage driver selection for the parameterized application.
    database : Database
        Database driver selection for the parameterized application.
    """
    plan = InstallationPlan(
        "test-app",
        tmp_path / "destination with spaces \u00f1 &",
        storage=storage,
        database=database,
    )
    runner = FixtureInstallerRunner(plan)
    steps = []
    result = Installer(plan, tools, runner, on_step=steps.append).install()
    assert result.creation == State.COMPLETED and result.published
    assert result.python_version == "3.14.6" and result.framework_version == "0.801.0"
    sync_calls = [call for call in runner.calls if call[0][1] == "sync"]
    assert len(sync_calls) == 1
    assert sync_calls[0][0][1:] == ["sync", "--python", "3.14"]
    assert sync_calls[0][1] == plan.path
    assert (plan.path / ".python-version").read_text().strip() == "3.14"
    assert (plan.path / "uv.lock").is_file()
    assert (plan.path / ".env.example").read_bytes() == (FIXTURE / ".env.example").read_bytes()
    assert read_env(plan.path / ".env")["APP_KEY"] == "fixture-generated-key"
    provenance = json.loads((plan.path / ".orionis-install.json").read_text())
    assert provenance["branch"] == "master" and provenance["sha"] == "a" * 40
    assert provenance["extras"] == list(plan.extras)
    assert not any("fixture-generated-key" in step for step in steps)


@pytest.mark.parametrize("phase", ["sync", "metadata", "configuration", "key"])
def test_postpublication_failure_preserves_project_and_recovery(
    tmp_path, tools, clone_fixture, phase
):
    """Verify postpublication failure preserves project and recovery.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    tools : Prerequisites
        Executable paths used by the simulated installer.
    clone_fixture : None
        Fixture that replaces remote cloning with the offline template.
    phase : str
        Installation phase selected for failure or cancellation.
    """
    plan = InstallationPlan("app", tmp_path / "app")
    runner = FixtureInstallerRunner(plan, fail_phase=phase)
    with pytest.raises(InstallerError) as raised:
        Installer(plan, tools, runner).install()
    assert raised.value.exit_code == 1
    assert MESSAGES["installation_recovery"].format(path=plan.path) in str(
        raised.value
    ) and "uv sync --python 3.14" in str(raised.value)
    assert (plan.path / "reactor").exists() and (plan.path / ".env").exists()
    if phase == "sync":
        assert (plan.path / "user-during-install.txt").read_text() == "keep this user file"
    assert not list(tmp_path.glob(".*.orionis-*/"))


def test_clone_failure_never_publishes_and_cleans_only_owned_staging(tmp_path, tools, monkeypatch):
    """Verify clone failure never publishes and cleans only owned staging.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    tools : Prerequisites
        Executable paths used by the simulated installer.
    monkeypatch : pytest.MonkeyPatch
        Scoped replacement fixture for controlled dependencies.
    """
    plan = InstallationPlan("app", tmp_path / "app")
    unrelated = tmp_path / "my-notes.txt"
    unrelated.write_text("keep")

    def clone(staging, git, runner):
        """Create a partial clone and fail before publication.

        Parameters
        ----------
        staging : Path
            Owned staging directory used by the clone fixture.
        git : Path
            Trusted Git executable supplied to the clone fixture.
        runner : object
            Process fixture supplied to the clone seam.

        Raises
        ------
        ProcessError
            Always raise the injected clone failure.
        """
        (staging / "partial.txt").write_text("partial")
        raise ProcessError("fixture clone failure")

    monkeypatch.setattr(installer, "clone_skeleton", clone)
    with pytest.raises(ProcessError):
        Installer(plan, tools, FixtureInstallerRunner(plan)).install()
    assert not plan.path.exists()
    assert unrelated.read_text() == "keep"
    assert not list(tmp_path.glob(".*orionis*"))


@pytest.mark.parametrize("phase", ["sync", "metadata", "configuration", "key"])
def test_cancellation_after_publication_preserves_application(
    tmp_path, tools, clone_fixture, phase
):
    """Verify cancellation after publication preserves application.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    tools : Prerequisites
        Executable paths used by the simulated installer.
    clone_fixture : None
        Fixture that replaces remote cloning with the offline template.
    phase : str
        Installation phase selected for failure or cancellation.
    """
    plan = InstallationPlan("app", tmp_path / "app")
    with pytest.raises(Cancelled) as raised:
        Installer(plan, tools, FixtureInstallerRunner(plan, cancellation=phase)).install()
    assert raised.value.exit_code == 130
    assert plan.path.is_dir() and (plan.path / "reactor").is_file()
    assert MESSAGES["installation_recovery"].format(path=plan.path) in str(raised.value)


def test_cancellation_before_publication_cleans_only_staging(tmp_path, tools, monkeypatch):
    """Verify cancellation before publication cleans only staging.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    tools : Prerequisites
        Executable paths used by the simulated installer.
    monkeypatch : pytest.MonkeyPatch
        Scoped replacement fixture for controlled dependencies.
    """
    plan = InstallationPlan("app", tmp_path / "app")

    def clone(staging, git, runner):
        """Cancel cloning before any application files are published.

        Parameters
        ----------
        staging : Path
            Owned staging directory used by the clone fixture.
        git : Path
            Trusted Git executable supplied to the clone fixture.
        runner : object
            Process fixture supplied to the clone seam.

        Raises
        ------
        Cancelled
            Always raise the injected clone cancellation.
        """
        raise Cancelled("fixture clone cancellation")

    monkeypatch.setattr(installer, "clone_skeleton", clone)
    with pytest.raises(Cancelled):
        Installer(plan, tools, FixtureInstallerRunner(plan)).install()
    assert not plan.path.exists() and not list(tmp_path.glob(".*orionis*"))


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-python",
        "redirected-prefix",
        "version",
        "missing-extra",
        "missing-driver",
        "aggregate",
        "missing-package",
        "bad-package-version",
    ],
)
def test_metadata_requires_real_compatible_environment_and_effective_extra_packages(
    tmp_path, mutation
):
    """Verify environment identity and all effective framework extra dependencies.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    mutation : str
        Metadata inconsistency introduced into the verified response.
    """
    plan = InstallationPlan("app", tmp_path / "app", storage=Storage.ALL, database=Database.ALL)
    data = copy.deepcopy(fixture_metadata(plan))
    if mutation == "wrong-python":
        data["python"] = "3.13.9"
    elif mutation == "redirected-prefix":
        data["prefix"] = str(tmp_path / "uvx-global-environment")
    elif mutation == "version":
        data["version"] = "0.800.0"
    elif mutation == "missing-extra":
        data["extras"].remove("factories")
    elif mutation == "missing-driver":
        data["extras"].remove("redshift")
    elif mutation == "aggregate":
        data["requirements"] = [
            raw.replace(" or extra == 'database'", "")
            if raw.startswith("redshift-connector")
            else raw
            for raw in data["requirements"]
        ]
    elif mutation == "missing-package":
        del data["installed"]["faker"]
    elif mutation == "bad-package-version":
        data["installed"]["faker"] = "1.0.0"
    with pytest.raises(CompatibilityError):
        verify_metadata(data, plan, Requirement("orionis[database,factories,storage]>=0.801.0"))


def test_factories_are_verified_even_for_local_sqlite(tmp_path):
    """Verify that local SQLite applications include the factories dependencies.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    plan = InstallationPlan("app", tmp_path / "app")
    data = fixture_metadata(plan)
    del data["installed"]["faker"]
    with pytest.raises(CompatibilityError):
        verify_metadata(data, plan, Requirement("orionis[factories]>=0.801.0"))


def test_existing_app_key_is_not_regenerated_or_printed(tmp_path):
    """Verify existing APP_KEY is not regenerated or printed.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    root = tmp_path / "app"
    root.mkdir()
    (root / ".env").write_text("APP_KEY='existing-fixture-private-key'\n")
    plan = InstallationPlan("app", root)
    runner = FixtureInstallerRunner(plan)
    ensure_app_key(runner, Path(sys.executable), root)
    assert read_env(root / ".env")["APP_KEY"] == "existing-fixture-private-key"
    assert not any("key:generate" in call[0] or "--force" in call[0] for call in runner.calls)


def test_bootstrap_cannot_change_existing_app_key_without_diagnostic(tmp_path):
    """Verify bootstrap cannot change existing APP_KEY without diagnostic.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    (tmp_path / ".env").write_text("APP_KEY='before'\n")

    class BadBootstrap:
        """Simulate a bootstrap that changes an existing application key."""

        def run(self, argv, **kwargs):
            """Modify the existing application key during simulated bootstrap.

            Parameters
            ----------
            argv : Sequence[str or Path]
                Separate command arguments supplied to the process fixture.
            **kwargs : dict
                Additional options accepted by the controlled fixture.
            """
            (tmp_path / ".env").write_text("APP_KEY='after'\n")

    with pytest.raises(CompatibilityError, match=re.escape(MESSAGES["app_key_changed"])):
        ensure_app_key(BadBootstrap(), Path(sys.executable), tmp_path)


@pytest.mark.parametrize(
    "key,value",
    [("database", "pgsql"), ("driver", "pgsql"), ("storage", "s3"), ("name", "wrong-name")],
)
def test_effective_configuration_must_match_plan(tmp_path, key, value):
    """Verify effective configuration must match plan.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    key : str
        Effective configuration field selected for modification.
    value : str
        Incompatible effective configuration value.
    """
    plan = InstallationPlan("app", tmp_path / "app")
    data = {"database": "sqlite", "driver": "sqlite", "storage": "local", "name": "app"}
    data[key] = value
    with pytest.raises(CompatibilityError):
        verify_configuration(data, plan)
