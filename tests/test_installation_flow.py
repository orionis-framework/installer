"""Verify stack preparation and editor-independent post-install behavior."""

import sys
from pathlib import Path
from unittest.mock import Mock, patch
import pytest
from typer.testing import CliRunner
from orionis_installer.cli import app
from orionis_installer.configuration import PORTS, configure_environment, read_env
from orionis_installer.exceptions import CompatibilityError, ProcessError
from orionis_installer.installer import ensure_app_key
from orionis_installer.models import (
    Database,
    InstallationPlan,
    InstallationResult,
    PostInstallOptions,
    Stack,
    State,
)
from orionis_installer.post_install import migration_commands, run_post_install
from orionis_installer.prerequisites import Prerequisites
from orionis_installer.processes import Runner


@pytest.mark.parametrize("flag", ["--open", "--no-open"])
def test_removed_editor_flags_are_rejected(flag: str) -> None:
    """Reject retired editor options before creating a project.

    Parameters
    ----------
    flag : str
        Former editor launch option.
    """
    result = CliRunner().invoke(app, ["new", "example", flag, "--no-interaction"])
    assert result.exit_code == 2
    assert "No such option" in result.output


@pytest.mark.parametrize("stack", list(Stack))
def test_stack_migrations_do_not_execute_seeders(tmp_path: Path, stack: Stack) -> None:
    """Prepare each stack's required tables without running application seeders.

    Parameters
    ----------
    tmp_path : Path
        Disposable application directory containing environment configuration.
    stack : Stack
        Application variant whose database setup is being requested.
    """
    queue = "database" if stack == Stack.SLIM_WORKER else "sync"
    (tmp_path / ".env").write_text(
        f"DB_CONNECTION=sqlite\nDB_DATABASE=database/database.sqlite\nQUEUE_CONNECTION={queue}\n",
        encoding="utf-8",
    )
    result = InstallationResult(
        InstallationPlan(name="example", path=tmp_path, stack=stack),
        creation=State.COMPLETED,
    )
    prerequisites = Prerequisites(
        tmp_path / "uv",
        tmp_path / "git",
        Path(sys.executable),
        "3.14.3",
    )
    runner, ui = Mock(spec=Runner), Mock()
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    with patch("orionis_installer.post_install.project_python", return_value=python):
        run_post_install(
            result,
            PostInstallOptions(migrate=True),
            prerequisites,
            runner,
            ui,
            no_interaction=True,
        )
    expected = [] if stack == Stack.SLIM_REALTIME else ["migrate"]
    if stack in {Stack.MASTER, Stack.SSR}:
        expected.append("auth:migrate")
    if stack == Stack.SLIM_WORKER:
        expected.append("queue:migrate")
    assert [call.args[0] for call in runner.run.call_args_list] == [
        [python, "-B", "reactor", command] for command in expected
    ]
    expected_state = State.COMPLETED if expected else State.SKIPPED
    assert result.migrations == expected_state
    assert result.exit_code == 0
    ui.confirm.assert_not_called()


@pytest.mark.parametrize(
    "key,command",
    [
        ("CACHE_STORE", "cache:migrate"),
        ("QUEUE_CONNECTION", "queue:migrate"),
        ("TASKS_STORE", "schedule:migrate"),
        ("SESSION_DRIVER", "session:migrate"),
    ],
)
def test_selected_database_services_receive_their_migrations(
    tmp_path: Path,
    key: str,
    command: str,
) -> None:
    """Include the migration for a service configured with a database backend.

    Parameters
    ----------
    tmp_path : Path
        Disposable application environment directory.
    key : str
        Service backend selector.
    command : str
        Framework command that creates the selected service's tables.
    """
    (tmp_path / ".env").write_text(f"{key}='str:database'\n", encoding="utf-8")
    result = InstallationResult(InstallationPlan(name="example", path=tmp_path))
    assert migration_commands(result) == ("migrate", command)


def test_realtime_application_migrations_are_included_when_added(tmp_path: Path) -> None:
    """Honor application migrations added to a realtime project.

    Parameters
    ----------
    tmp_path : Path
        Disposable realtime project with a custom migrations directory.
    """
    (tmp_path / ".env").write_text("", encoding="utf-8")
    (tmp_path / "database" / "migrations").mkdir(parents=True)
    result = InstallationResult(
        InstallationPlan(name="example", path=tmp_path, stack=Stack.SLIM_REALTIME),
    )
    assert migration_commands(result) == ("migrate",)


def test_internal_migration_failure_preserves_created_project(tmp_path: Path) -> None:
    """Report a framework migration failure after applying application migrations.

    Parameters
    ----------
    tmp_path : Path
        Disposable full application directory.
    """
    (tmp_path / ".env").write_text(
        "DB_CONNECTION=sqlite\nDB_DATABASE=database/database.sqlite\n",
        encoding="utf-8",
    )
    result = InstallationResult(
        InstallationPlan(name="example", path=tmp_path, stack=Stack.MASTER),
        creation=State.COMPLETED,
    )
    prerequisites = Prerequisites(
        tmp_path / "uv",
        tmp_path / "git",
        Path(sys.executable),
        "3.14.3",
    )
    runner, ui = Mock(spec=Runner), Mock()
    runner.run.side_effect = [None, ProcessError("Migration failed")]
    with patch("orionis_installer.post_install.project_python", return_value=Path(sys.executable)):
        run_post_install(
            result,
            PostInstallOptions(migrate=True),
            prerequisites,
            runner,
            ui,
            no_interaction=True,
        )
    assert result.creation == State.COMPLETED
    assert result.migrations == State.FAILED
    assert result.exit_code == 3
    assert (tmp_path / ".env").is_file()
    assert [call.args[0][-1] for call in runner.run.call_args_list] == ["migrate", "auth:migrate"]
    assert "reported migration commands" in result.warnings[0]
    assert "auth:migrate" in ui.message.call_args.args[0]


@pytest.mark.parametrize("database", list(PORTS))
def test_interactive_connection_writes_direct_values(tmp_path: Path, database: Database) -> None:
    """Write prompted connection fields directly before running migrations.

    Parameters
    ----------
    tmp_path : Path
        Isolated application directory containing a minimal environment example.
    database : Database
        External connection requiring credentials, including Oracle service names.
    """
    (tmp_path / ".env.example").write_text(
        'APP_NAME=Template\nAPP_KEY="base64:dGVzdC1rZXk="\n', encoding="utf-8"
    )
    plan = InstallationPlan(name="example", path=tmp_path, database=database)
    configure_environment(tmp_path, plan)
    result = InstallationResult(plan, creation=State.COMPLETED)
    prerequisites = Prerequisites(tmp_path / "uv", tmp_path / "git", Path(sys.executable), "3.14.3")
    runner, ui = Mock(spec=Runner), Mock()
    secret = 'it\'s "quoted" \\ # direct'
    ui.confirm.return_value = True
    ui.text.side_effect = [
        "db.internal",
        str(PORTS[database]),
        "application-data",
        "operator",
        secret,
    ]

    with patch("orionis_installer.post_install.project_python", return_value=Path(sys.executable)):
        run_post_install(
            result,
            PostInstallOptions(git=False, migrate=True),
            prerequisites,
            runner,
            ui,
            no_interaction=False,
        )

    path = tmp_path / ".env"
    values = read_env(path)
    database_key = "DB_SERVICE_NAME" if database == Database.ORACLE else "DB_DATABASE"
    assert values["DB_HOST"] == "db.internal"
    assert values["DB_PORT"] == str(PORTS[database])
    assert values[database_key] == "application-data"
    assert values["DB_USERNAME"] == "operator"
    assert values["DB_PASSWORD"] == secret
    assert values["APP_KEY"] == "base64:dGVzdC1rZXk="
    assert {key for key, value in values.items() if value and value.startswith("base64:")} == {
        "APP_KEY"
    }
    assert f"DB_PORT={PORTS[database]}\n" in path.read_text(encoding="utf-8")
    assert ui.text.call_args.kwargs["password"] is True
    assert result.migrations == State.COMPLETED
    ui.warning.assert_not_called()


@pytest.mark.parametrize("before", ["", "none", "null", "nil", "NaN"])
def test_generated_application_key_uses_double_quotes(tmp_path: Path, before: str) -> None:
    """Apply the shared quoting policy after the framework generates a missing key.

    Parameters
    ----------
    tmp_path : Path
        Isolated application directory containing a missing key.
    before : str
        Empty or null-like value recognized by the framework.
    """
    path = tmp_path / ".env"
    original = f'# Application\nAPP_NAME="example"\nAPP_KEY={before} # local key\n'
    path.write_text(original, encoding="utf-8")
    key = "base64:dGVzdC1rZXk="
    runner = Mock(spec=Runner)
    python = Path(sys.executable)

    def generate(argv: list[Path | str], *, cwd: Path, timeout: int) -> None:
        """Simulate the framework's default single-quoted dotenv writer."""
        assert argv == [python, "-B", "reactor", "key:generate"]
        assert cwd == tmp_path
        assert timeout == 90
        path.write_text(
            original.replace(f"APP_KEY={before}", f"APP_KEY='{key}'"),
            encoding="utf-8",
        )

    runner.run.side_effect = generate

    ensure_app_key(runner, python, tmp_path)

    assert read_env(path)["APP_KEY"] == key
    assert path.read_text(encoding="utf-8") == original.replace(
        f"APP_KEY={before}", f'APP_KEY="{key}"'
    )
    runner.run.assert_called_once_with(
        [python, "-B", "reactor", "key:generate"], cwd=tmp_path, timeout=90
    )


@pytest.mark.parametrize("stored", ["'existing-key'", '"base64:dGVzdC1rZXk="', '"str:null"'])
def test_existing_application_key_is_not_rewritten(tmp_path: Path, stored: str) -> None:
    """Preserve existing key values and their representation during bootstrap.

    Parameters
    ----------
    tmp_path : Path
        Isolated application directory containing an existing key.
    stored : str
        Existing dotenv representation that must remain byte-for-byte unchanged.
    """
    path = tmp_path / ".env"
    original = f"APP_KEY={stored} # preserve\n".encode()
    path.write_bytes(original)
    runner = Mock(spec=Runner)
    python = Path(sys.executable)

    ensure_app_key(runner, python, tmp_path)

    assert path.read_bytes() == original
    runner.run.assert_called_once_with(
        [python, "-B", "-c", "from bootstrap.app import app"], cwd=tmp_path, timeout=90
    )


def test_application_key_generation_failure_is_not_hidden(tmp_path: Path) -> None:
    """Reject a successful command that does not actually generate an application key.

    Parameters
    ----------
    tmp_path : Path
        Isolated application directory whose key remains empty.
    """
    path = tmp_path / ".env"
    original = b"APP_KEY= # missing\n"
    path.write_bytes(original)

    with pytest.raises(CompatibilityError):
        ensure_app_key(Mock(spec=Runner), Path(sys.executable), tmp_path)

    assert path.read_bytes() == original
