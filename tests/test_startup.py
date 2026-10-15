"""Verify noninteractive startup and driver-default validation."""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orionis_installer import __version__
from orionis_installer.cli import app
from orionis_installer.exceptions import ValidationError
from orionis_installer.models import STACKS, Database, InstallationPlan, Storage


def test_ui_does_not_import_prompt_toolkit() -> None:
    """Keep prompt-toolkit unloaded when constructing noninteractive output."""
    source = (
        'import sys; from io import StringIO; from orionis_installer.ui import UI; '
        'ui = UI(file=StringIO()); assert "prompt_toolkit" not in sys.modules; '
        'assert ui.prompts is ui.prompts; assert "prompt_toolkit" in sys.modules'
    )
    result = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("arguments", [["--version"], ["new", "--help"]])
def test_cli_dispatch(arguments: list[str]) -> None:
    """Preserve CLI dispatch and help after internal method renaming.

    Parameters
    ----------
    arguments : list[str]
        Public command arguments that do not create an application.
    """
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 0, result.output


def test_version_matches_project_metadata() -> None:
    """Keep the package and CLI versions aligned with the release manifest."""
    manifest_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with manifest_path.open("rb") as manifest:
        version = tomllib.load(manifest)["project"]["version"]
    assert __version__ == version

    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"orionis-installer {version}"


@pytest.mark.parametrize("shell_name", ["pwsh", "powershell"])
def test_release_downloads_every_catalog_source(tmp_path: Path, shell_name: str) -> None:
    """Execute release checkout statements without allowing Git or publishing.

    Parameters
    ----------
    tmp_path : Path
        Audit destination recorded by the mocked Git command.
    shell_name : str
        PowerShell edition whose native argument handling is exercised.
    """
    project = Path(__file__).resolve().parents[1]
    if not (project / "RELEASE.ps1").is_file():
        pytest.skip("The local release script is not included in this checkout")
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} is required to validate the release script")
    script = r"""
$ErrorActionPreference = 'Stop'
$ProjectRoot = $env:ORIONIS_TEST_PROJECT_ROOT
$Uv = $env:ORIONIS_TEST_PYTHON
$Git = 'git-not-executed'
$TestRoot = $env:ORIONIS_TEST_AUDIT_ROOT
$clones = [System.Collections.Generic.List[object]]::new()
function Invoke-ReleaseCommand {
    param([string]$Executable, [string[]]$Arguments)
    if ($Executable -eq $Uv) {
        $code = $Arguments[-1]
        & $Uv -c $code
        if ($LASTEXITCODE -ne 0) { throw 'Cannot read the stack catalog' }
    } elseif ($Executable -eq $Git) {
        $clones.Add($Arguments)
    } else {
        throw "Unexpected executable: $Executable"
    }
}
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $ProjectRoot 'RELEASE.ps1'), [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count) { throw ($parseErrors -join "`n") }
$assignments = $ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
    $node.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
    $node.Left.VariablePath.UserPath -in @('skeletonCatalogJson', 'skeletonCatalog')
}, $true)
foreach ($statement in $assignments) {
    . ([scriptblock]::Create($statement.Extent.Text))
}
$cloneLoop = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.ForEachStatementAst] -and
    $node.Extent.Text.Contains("'clone'")
}, $true)
if ($null -eq $cloneLoop) { throw 'Missing release checkout loop' }
. ([scriptblock]::Create($cloneLoop.Extent.Text))
ConvertTo-Json -InputObject $clones.ToArray() -Depth 3 -Compress
"""
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, check=False,
        env=os.environ | {
            "ORIONIS_TEST_PROJECT_ROOT": str(project),
            "ORIONIS_TEST_PYTHON": sys.executable,
            "ORIONIS_TEST_AUDIT_ROOT": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        [
            "clone", "--depth", "1", "--single-branch", "--branch", source.branch,
            "--no-recurse-submodules", source.repository, str(tmp_path / stack.value),
        ]
        for stack, source in STACKS.items()
    ]


@pytest.mark.parametrize("field", ["default_storage", "default_database"])
def test_driver_defaults_reject_strings(tmp_path: Path, field: str) -> None:
    """Reject values that cannot provide the concrete driver's enum contract.

    Parameters
    ----------
    tmp_path : Path
        Isolated application destination.
    field : str
        Driver-default field supplied with an invalid runtime value.
    """
    with pytest.raises(ValidationError):
        InstallationPlan(
            name="example", path=tmp_path, storage=Storage.ALL, database=Database.ALL,
            **{field: "invalid"},
        )
