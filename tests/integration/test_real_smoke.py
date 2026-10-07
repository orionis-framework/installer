"""Verify the official skeleton, uv, factories and disposable SQLite when enabled."""

import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from orionis_installer.configuration import literal_value, read_env
from orionis_installer.installer import Installer, project_python
from orionis_installer.models import InstallationPlan, PostInstallOptions, Stack, State
from orionis_installer.post_install import run_post_install
from orionis_installer.prerequisites import check_prerequisites
from orionis_installer.processes import Runner


class RecordingPostUI:
    """Record migration output while forbidding input in a non-interactive smoke test."""

    def __init__(self) -> None:
        """Initialize isolated migration messages and warnings."""
        self.messages: list[str] = []
        self.warnings: list[str] = []

    def confirm(self, label: str, default: bool) -> bool:
        """Fail if the non-interactive smoke test unexpectedly asks for confirmation.

        Parameters
        ----------
        label : str
            Unexpected confirmation label supplied by the installer.
        default : bool
            Proposed confirmation fallback, which this test never accepts.

        Raises
        ------
        pytest.fail.Exception
            Always, because smoke migration settings must be complete.
        """
        pytest.fail(f"Unexpected migration confirmation: {label}")

    def text(
        self,
        label: str,
        default: str = "",
        *,
        validator: Callable[[str], object] | None = None,
        password: bool = False,
    ) -> str:
        """Fail if the non-interactive smoke test unexpectedly asks for connection input.

        Parameters
        ----------
        label : str
            Unexpected connection field supplied by the installer.
        default : str, optional
            Proposed input default, which this test never accepts.
        validator : Callable[[str], object] or None, optional
            Validation callback accepted for protocol compatibility.
        password : bool, optional
            Whether the unexpected field would contain a password.

        Raises
        ------
        pytest.fail.Exception
            Always, because disposable SQLite requires no credentials.
        """
        pytest.fail(f"Unexpected migration input: {label}")

    def message(self, value: str) -> None:
        """Capture a migration progress message.

        Parameters
        ----------
        value : str
            Installer message displayed before migration execution.
        """
        self.messages.append(value)

    def warning(self, value: str) -> None:
        """Capture a migration warning for the result assertions.

        Parameters
        ----------
        value : str
            Installer diagnostic explaining a failed post-install operation.
        """
        self.warnings.append(value)


@pytest.mark.smoke
@pytest.mark.parametrize("stack", list(Stack))
@pytest.mark.skipif(
    os.environ.get("ORIONIS_REAL_SMOKE") != "1",
    reason="Explicitly set ORIONIS_REAL_SMOKE=1 to use the network and uv.",
)
def test_real_uv_sqlite_and_factories_in_disposable_application(tmp_path, stack):
    """
    Create a real application and migrate its disposable SQLite database safely.

    Parameters
    ----------
    tmp_path : Path
        Isolated directory for the application and its database.
    stack : Stack
        Application stack whose exact configured branch must be installed.
    """
    runner = Runner()
    prerequisites = check_prerequisites(runner, cwd=tmp_path)
    plan = InstallationPlan("smoke-app", tmp_path / "real SQLite disposable app", stack=stack)
    result = Installer(plan, prerequisites, runner).install()
    assert result.creation == State.COMPLETED
    assert result.python_version.startswith("3.14.")
    assert result.framework_version
    assert (plan.path / "uv.lock").is_file()
    environment = read_env(plan.path / ".env")
    assert environment["APP_KEY"]
    assert environment["DB_CONNECTION"] == "sqlite"
    provenance = json.loads((plan.path / ".orionis-install.json").read_text(encoding="utf-8"))
    assert provenance["stack"] == stack.value
    assert provenance["skeleton"] == plan.source.repository
    assert provenance["branch"] == plan.source.branch
    runner.run(
        [project_python(plan.path), "-B", "-c", "import faker; assert faker.Faker().name()"],
        cwd=plan.path,
        timeout=30,
    )
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
    ui = RecordingPostUI()
    run_post_install(
        result,
        PostInstallOptions(git=False, migrate=True, open=False),
        prerequisites,
        runner,
        ui,
        no_interaction=True,
    )
    assert result.migrations == State.COMPLETED and result.exit_code == 0
    assert ui.messages and not ui.warnings and not result.warnings
    database = plan.path / Path(literal_value(environment["DB_DATABASE"]))
    assert database.is_relative_to(plan.path) and database.is_file()
    with closing(sqlite3.connect(database)) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        applied = connection.execute("SELECT COUNT(*) FROM migrations").fetchone()[0]
        users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert {"migrations", "users", "scheduler_tasks", "cache"} <= {name for (name,) in tables}
    assert applied > 0 and users == 0
