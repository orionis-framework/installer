"""Verify the official skeleton, uv, factories and disposable SQLite when enabled."""

import os
import sqlite3
from pathlib import Path

import pytest

from orionis_installer.configuration import literal_value, read_env
from orionis_installer.exceptions import CompatibilityError
from orionis_installer.installer import Installer, project_python
from orionis_installer.models import InstallationPlan, State
from orionis_installer.post_install import seeder_safety
from orionis_installer.prerequisites import check_prerequisites
from orionis_installer.processes import Runner


@pytest.mark.smoke
@pytest.mark.skipif(
    os.environ.get("ORIONIS_REAL_SMOKE") != "1",
    reason="Explicitly set ORIONIS_REAL_SMOKE=1 to use the network and uv.",
)
def test_real_uv_sqlite_and_factories_in_disposable_application(tmp_path):
    """
    Create a real application and migrate its disposable SQLite database safely.

    Parameters
    ----------
    tmp_path : Path
        Isolated directory for the application and its database.
    """
    runner = Runner()
    prerequisites = check_prerequisites(runner, cwd=tmp_path)
    plan = InstallationPlan("smoke-app", tmp_path / "real SQLite disposable app")
    result = Installer(plan, prerequisites, runner).install()
    assert result.creation == State.COMPLETED
    assert result.python_version.startswith("3.14.")
    assert result.framework_version
    assert (plan.path / "uv.lock").is_file()
    environment = read_env(plan.path / ".env")
    assert environment["APP_KEY"]
    assert environment["DB_CONNECTION"] == "sqlite"
    runner.run(
        [project_python(plan.path), "-B", "-c", "import faker; assert faker.Faker().name()"],
        cwd=plan.path,
        timeout=30,
    )
    # master currently has a static administrative seeder; certify the guard.
    with pytest.raises(CompatibilityError, match="administrator seeder"):
        seeder_safety(plan.path)
    invalid_connection = runner.run(
        [
            project_python(plan.path),
            "-B",
            "reactor",
            "migrate",
            "--database",
            "installer_nonexistent_fixture_connection",
        ],
        cwd=plan.path,
        timeout=60,
        check=False,
    )
    assert invalid_connection.returncode == 1
    # Migration-only is explicit in this isolated test. Never seed example users.
    runner.run([project_python(plan.path), "-B", "reactor", "migrate"], cwd=plan.path, timeout=180)
    database = plan.path / Path(literal_value(environment["DB_DATABASE"]))
    assert database.is_relative_to(plan.path) and database.is_file()
    with sqlite3.connect(database) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        applied = connection.execute("SELECT COUNT(*) FROM migrations").fetchone()[0]
        users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert {"migrations", "users", "scheduler_tasks", "cache"} <= {name for (name,) in tables}
    assert applied > 0 and users == 0
