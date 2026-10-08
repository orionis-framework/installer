"""Verify dependency checks without repeated requirement parsing."""

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock, patch

import pytest
from packaging.requirements import Requirement

from orionis_installer.exceptions import CompatibilityError
from orionis_installer.installer import probe_json, verify_configuration, verify_metadata
from orionis_installer.models import Database, InstallationPlan, Storage


@pytest.mark.parametrize("installed", [{"core": "1.2", "driver": "2"}, {"core": "0.1"}])
def test_metadata_parses_requirements_once(tmp_path: Path, installed: dict[str, str]) -> None:
    """Parse each dependency once while enforcing selected markers and versions.

    Parameters
    ----------
    tmp_path : Path
        Isolated project destination.
    installed : dict[str, str]
        Installed dependency versions, including an invalid resolution.
    """
    plan = InstallationPlan(name="example", path=tmp_path)
    requirement = Requirement("orionis[factories]>=1")
    requirements = ["core>=1", "driver>=2; extra == 'factories'", "absent; extra == 's3'"]
    data = {
        "python": "3.14.0", "prefix": str(tmp_path / ".venv"), "version": "1.0",
        "extras": ["factories"], "requirements": requirements, "installed": installed,
    }
    with patch("orionis_installer.installer.Requirement", wraps=Requirement) as parser:
        if "driver" in installed:
            verify_metadata(data, plan, requirement)
        else:
            with pytest.raises(CompatibilityError):
                verify_metadata(data, plan, requirement)
        assert parser.call_count == len(requirements)


@pytest.mark.parametrize("output", ["[]", "null", "42", "", "not-json"])
def test_probe_rejects_non_mapping(output: str, tmp_path: Path) -> None:
    """Reject probe output that cannot satisfy the metadata mapping contract.

    Parameters
    ----------
    output : str
        Invalid probe output emitted by a child process.
    tmp_path : Path
        Isolated working directory for the probe.
    """
    runner = Mock()
    runner.run.return_value = CompletedProcess([], 0, stdout=output)
    with pytest.raises(CompatibilityError):
        probe_json(runner, tmp_path / "python", tmp_path, "pass")


@pytest.mark.parametrize("driver", ["aws", "s3", "azure"])
def test_s3_uses_framework_driver_name(tmp_path: Path, driver: str) -> None:
    """Match the S3 disk to its actual AWS driver rather than its menu identifier.

    Parameters
    ----------
    tmp_path : Path
        Isolated project destination.
    driver : str
        Driver identifier returned by the project's configuration probe.
    """
    plan = InstallationPlan(name="example", path=tmp_path, storage=Storage.S3)
    data = {
        "name": plan.name, "database": "sqlite", "driver": "sqlite",
        "storage": "s3", "storage_driver": driver,
    }
    if driver == "aws":
        verify_configuration(data, plan)
    else:
        with pytest.raises(CompatibilityError):
            verify_configuration(data, plan)


@pytest.mark.parametrize("complete", [True, False])
def test_aggregate_preserves_driver_requirements(tmp_path: Path, complete: bool) -> None:
    """Reject aggregates that omit a dependency required by a concrete driver.

    Parameters
    ----------
    tmp_path : Path
        Isolated project destination.
    complete : bool
        Whether the database aggregate includes the MySQL dependency.
    """
    plan = InstallationPlan(name="example", path=tmp_path, database=Database.ALL)
    marker = "extra == 'mysql'"
    if complete:
        marker += " or extra == 'database'"
    data = {
        "python": "3.14.0", "prefix": str(tmp_path / ".venv"), "version": "1.0",
        "extras": ["database", "factories", "mysql", "pgsql", "oracle", "sqlserver", "redshift"],
        "requirements": ["core>=1", f"mysql-driver>=2; {marker}"],
        "installed": {"core": "1", "mysql-driver": "2"},
    }
    requirement = Requirement("orionis[database,factories]>=1")
    if complete:
        verify_metadata(data, plan, requirement)
    else:
        with pytest.raises(CompatibilityError):
            verify_metadata(data, plan, requirement)


