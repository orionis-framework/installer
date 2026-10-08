"""Validate real skeleton branches without using external application services."""

import ast
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from orionis_installer.configuration import (
    configure_environment, configure_pyproject, ensure_gitignore, literal_value, read_env,
)
from orionis_installer.installer import (
    CONFIG_PROBE, Installer, probe_json, project_python, verify_configuration,
)
from orionis_installer.models import (
    Database, InstallationPlan, PostInstallOptions, Stack, State, Storage,
)
from orionis_installer.post_install import connection_ready, connection_values, run_post_install
from orionis_installer.prerequisites import check_prerequisites
from orionis_installer.processes import Runner
from orionis_installer.skeleton import validate_tree
from orionis_installer.ui import UI

STORAGE_SELECTIONS = [(value, None) for value in Storage] + [
    (Storage.ALL, value) for value in Storage if value != Storage.ALL
]
DATABASE_SELECTIONS = [(value, None) for value in Database] + [
    (Database.ALL, value) for value in Database if value != Database.ALL
]


@pytest.fixture(params=list(Stack))
def skeleton_root(request: pytest.FixtureRequest) -> Path:
    """Locate a separately downloaded skeleton branch for read-only inspection.

    Parameters
    ----------
    request : pytest.FixtureRequest
        Stack selected for this fixture invocation.

    Returns
    -------
    Path
        Downloaded branch under the explicitly configured audit directory.
    """
    root = os.environ.get("ORIONIS_SKELETON_AUDIT")
    if not root:
        pytest.skip("Set ORIONIS_SKELETON_AUDIT to downloaded blank/ssr directories")
    path = Path(root) / request.param.value
    assert path.is_dir()
    return path


@pytest.mark.parametrize("storage,default_storage", STORAGE_SELECTIONS)
@pytest.mark.parametrize("database,default_database", DATABASE_SELECTIONS)
def test_real_environment_matrix(
    skeleton_root: Path, tmp_path: Path, storage: Storage, default_storage: Storage | None,
    database: Database, default_database: Database | None,
) -> None:
    """Generate each menu combination from both real minimal environment files.

    Parameters
    ----------
    skeleton_root : Path
        Read-only downloaded branch.
    tmp_path : Path
        Owned staging directory receiving configuration copies.
    storage : Storage
        Requested storage SDK selection.
    default_storage : Storage or None
        Concrete default when all storage SDKs are requested.
    database : Database
        Requested database driver selection.
    default_database : Database or None
        Concrete default when all database drivers are requested.
    """
    shutil.copytree(skeleton_root / "config", tmp_path / "config")
    (tmp_path / "database").mkdir()
    for name in ("pyproject.toml", ".env.example", ".gitignore"):
        shutil.copyfile(skeleton_root / name, tmp_path / name)
    original = (tmp_path / ".env.example").read_bytes()
    plan = InstallationPlan(
        name="matrix-example", path=tmp_path, storage=storage, database=database,
        default_storage=default_storage, default_database=default_database,
    )
    requirement = configure_pyproject(tmp_path, plan)
    configure_environment(tmp_path, plan)
    ensure_gitignore(tmp_path)
    values = read_env(tmp_path / ".env")
    assert values["FILESYSTEM_DISK"] == plan.active_storage.value
    assert values["DB_CONNECTION"] == plan.active_database.value
    assert literal_value(values["APP_NAME"]) == plan.name
    assert requirement.extras.issuperset(plan.extras)
    assert Requirement(str(requirement)).specifier.contains("0.805.0")
    assert (tmp_path / ".env.example").read_bytes() == original
    assert values.get("DB_PASSWORD") is None
    ready = connection_ready(connection_values(tmp_path))
    assert ready == (plan.active_database == Database.SQLITE)
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").endswith("!uv.lock\n")


def test_real_skeleton_structure(skeleton_root: Path) -> None:
    """Check every Python file's syntax and the separated route bootstrap contract.

    Parameters
    ----------
    skeleton_root : Path
        Read-only downloaded branch to inspect.
    """
    validate_tree(skeleton_root)
    for path in skeleton_root.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    tree = ast.parse((skeleton_root / "bootstrap" / "app.py").read_text(encoding="utf-8"))
    routing = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute) and node.func.attr == "withRouting"]
    assert len(routing) == 1
    paths = {keyword.arg: keyword.value.value for keyword in routing[0].keywords
             if isinstance(keyword.value, ast.Constant)}
    for kind in ("web", "api", "console", "websocket", "ai"):
        assert paths[kind] == f"routes/{kind}.py"
        assert (skeleton_root / paths[kind]).is_file()


@pytest.mark.smoke
@pytest.mark.parametrize("stack", list(Stack))
@pytest.mark.parametrize("all_drivers", [False, True])
def test_real_installation_and_sqlite_migrations(
    stack: Stack, all_drivers: bool, tmp_path: Path,
) -> None:
    """Install each official stack and migrate only its disposable SQLite database.

    Parameters
    ----------
    stack : Stack
        Official source branch to install through the complete workflow.
    all_drivers : bool
        Install every SDK and probe each concrete database and storage combination.
    tmp_path : Path
        Disposable parent containing the new application and its SQLite database.
    """
    if os.environ.get("ORIONIS_REAL_SMOKE") != "1":
        pytest.skip("Set ORIONIS_REAL_SMOKE=1 to allow real uv/network installation")
    runner = Runner()
    prerequisites = check_prerequisites(runner, cwd=tmp_path)
    plan = InstallationPlan(
        name="smoke-example", path=tmp_path / "application", stack=stack,
        storage=Storage.ALL if all_drivers else Storage.LOCAL,
        database=Database.ALL if all_drivers else Database.SQLITE,
    )
    result = Installer(plan, prerequisites, runner).install()
    run_post_install(
        result, PostInstallOptions(git=True, migrate=True, open=False), prerequisites,
        runner, UI(no_color=True), no_interaction=True,
    )
    assert result.creation == State.COMPLETED
    assert result.migrations == State.COMPLETED, result.warnings
    assert result.git == State.COMPLETED
    assert result.exit_code == 0
    values = read_env(plan.path / ".env")
    assert values["APP_KEY"]
    assert (plan.path / literal_value(values["DB_DATABASE"])).is_file()
    ignored = runner.run(
        [prerequisites.git, "check-ignore", "--no-index", ".env", ".venv/", "uv.lock"],
        cwd=plan.path,
    ).stdout.splitlines()
    assert set(ignored) == {".env", ".venv/"}
    revision = subprocess.run(
        [str(prerequisites.git), "rev-parse", "--is-inside-work-tree"], cwd=plan.path,
        capture_output=True, text=True, check=True,
    )
    assert revision.stdout.strip() == "true"
    if all_drivers:
        python = project_python(plan.path)
        for storage in Storage:
            if storage == Storage.ALL:
                continue
            for database in Database:
                if database == Database.ALL:
                    continue
                selected = InstallationPlan(
                    name=plan.name, path=plan.path, stack=stack,
                    storage=Storage.ALL, database=Database.ALL,
                    default_storage=storage, default_database=database,
                )
                configure_environment(plan.path, selected)
                verify_configuration(probe_json(runner, python, plan.path, CONFIG_PROBE), selected)
