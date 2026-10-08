"""Verify noninteractive startup and driver-default validation."""

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orionis_installer.cli import app
from orionis_installer.exceptions import ValidationError
from orionis_installer.models import Database, InstallationPlan, Storage


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
