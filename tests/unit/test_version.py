"""Keep release metadata and the public installer version consistent."""

import tomllib
from pathlib import Path

from orionis_installer import __version__


def test_package_version_matches_project_manifest():
    """Reject version drift before the release script builds distribution artifacts."""
    manifest_path = Path(__file__).parents[2] / "pyproject.toml"
    with manifest_path.open("rb") as manifest_file:
        project_version = tomllib.load(manifest_file)["project"]["version"]

    assert __version__ == project_version, (
        "Update project.version and orionis_installer.__version__ together before releasing."
    )
