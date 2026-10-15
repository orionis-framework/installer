"""Validate real skeleton branches without using external application services."""

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import tomlkit
from packaging.requirements import Requirement

from orionis_installer.configuration import (
    config_contract, configure_environment, configure_pyproject, ensure_gitignore, literal_value,
    orionis_requirement, read_env,
)
from orionis_installer.exceptions import CompatibilityError
from orionis_installer.installer import (
    CONFIG_PROBE, Installer, probe_json, project_python, verify_configuration,
)
from orionis_installer.models import (
    STACKS, Database, InstallationPlan, PostInstallOptions, Stack, State, Storage,
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


@pytest.fixture(scope="session")
def skeleton_roots() -> dict[Stack, Path]:
    """Check every configured audit checkout before running the real matrix.

    Returns
    -------
    dict[Stack, Path]
        Read-only checkouts indexed by their public stack identifiers.
    """
    root = os.environ.get("ORIONIS_SKELETON_AUDIT")
    if not root:
        pytest.skip("Set ORIONIS_SKELETON_AUDIT to downloaded stack directories")
    paths = {stack: Path(root) / stack.value for stack in STACKS}
    missing = [
        f"{stack.value} ({STACKS[stack].branch})"
        for stack, path in paths.items() if not path.is_dir()
    ]
    if missing:
        pytest.exit(
            f"ORIONIS_SKELETON_AUDIT is incomplete at {root}: missing {', '.join(missing)}. "
            "Download every catalog branch before running the skeleton audit.",
            returncode=2,
        )
    return paths


@pytest.fixture(params=list(Stack), scope="session")
def skeleton_root(request: pytest.FixtureRequest, skeleton_roots: dict[Stack, Path]) -> Path:
    """Locate a checked skeleton branch for read-only inspection.

    Parameters
    ----------
    request : pytest.FixtureRequest
        Stack selected for this fixture invocation.
    skeleton_roots : dict[Stack, Path]
        Complete set of separately downloaded catalog branches.

    Returns
    -------
    Path
        Downloaded branch under the explicitly configured audit directory.
    """
    return skeleton_roots[request.param]


def test_incomplete_skeleton_audit_fails_once(tmp_path: Path) -> None:
    """Report missing branches once instead of repeating every matrix setup error.

    Parameters
    ----------
    tmp_path : Path
        Incomplete audit directory reproducing the original release checkout set.
    """
    (tmp_path / Stack.BLANK.value).mkdir()
    (tmp_path / Stack.SSR.value).mkdir()
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            f"{Path(__file__).resolve()}::test_real_skeleton_structure",
            "-q", "-p", "no:cacheprovider",
        ],
        capture_output=True, text=True, check=False,
        env=os.environ | {"ORIONIS_SKELETON_AUDIT": str(tmp_path), "PYTEST_ADDOPTS": ""},
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2, output
    assert "slim_api (slim_api_1.x)" in output
    assert "slim_mcp (slim_mcp_1.x)" in output
    assert "ERROR at setup" not in output


@pytest.mark.parametrize("storage,default_storage", STORAGE_SELECTIONS)
@pytest.mark.parametrize("database,default_database", DATABASE_SELECTIONS)
def test_real_environment_matrix(
    skeleton_root: Path, tmp_path: Path, storage: Storage, default_storage: Storage | None,
    database: Database, default_database: Database | None,
) -> None:
    """Generate each menu combination from every real minimal environment file.

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
    if (skeleton_root / "database").is_dir():
        (tmp_path / "database").mkdir()
    for name in ("pyproject.toml", ".env.example", ".gitignore"):
        shutil.copyfile(skeleton_root / name, tmp_path / name)
    original = (tmp_path / ".env.example").read_bytes()
    original_requirement = orionis_requirement(
        tomlkit.parse((tmp_path / "pyproject.toml").read_text(encoding="utf-8")),
    )
    plan = InstallationPlan(
        name="matrix-example", path=tmp_path, storage=storage, database=database,
        default_storage=default_storage, default_database=default_database,
        stack=Stack(skeleton_root.name),
    )
    requirement = configure_pyproject(tmp_path, plan)
    configure_environment(tmp_path, plan)
    ensure_gitignore(tmp_path)
    values = read_env(tmp_path / ".env")
    assert values["FILESYSTEM_DISK"] == plan.active_storage.value
    assert values["DB_CONNECTION"] == plan.active_database.value
    assert literal_value(values["APP_NAME"]) == plan.name
    assert requirement.extras.issuperset(plan.extras)
    assert Requirement(str(requirement)).specifier == original_requirement.specifier
    assert requirement.marker == original_requirement.marker
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
    if skeleton_root.name == Stack.SLIM_MCP.value:
        kinds = {"ai"}
    elif skeleton_root.name == Stack.SLIM_API.value:
        kinds = {"api", "websocket"}
    else:
        kinds = {"web", "api", "console", "websocket", "ai"}
    assert set(paths) == kinds | {"health"}
    assert paths["health"] == "/up"
    for kind in kinds:
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


@pytest.mark.parametrize("storage,database", [
    (Storage.LOCAL, Database.SQLITE),
    (Storage.S3, Database.ORACLE),
])
def test_minimal_mcp_configuration(
    tmp_path: Path, storage: Storage, database: Database,
) -> None:
    """Configure Slim MCP without distributed database or filesystem overrides.

    Parameters
    ----------
    tmp_path : Path
        Minimal MCP staging directory matching the official branch structure.
    storage : Storage
        Selected filesystem driver.
    database : Database
        Selected database connection.
    """
    for name in ("pyproject.toml", "reactor", "bootstrap/app.py", "config/app.py",
                 "config/mcp.py", ".gitignore"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    (tmp_path / ".env.example").write_text(
        "APP_NAME=Orionis\nAPP_KEY=\nFILESYSTEM_DISK=local\n"
        "DB_CONNECTION=sqlite\nDB_DATABASE=database/database.sqlite\n",
        encoding="utf-8",
    )
    plan = InstallationPlan(
        name="mcp-example", path=tmp_path, stack=Stack.SLIM_MCP,
        storage=storage, database=database,
    )

    validate_tree(tmp_path)
    configure_environment(tmp_path, plan)
    ensure_gitignore(tmp_path)

    values = read_env(tmp_path / ".env")
    assert literal_value(values["APP_NAME"]) == plan.name
    assert values["FILESYSTEM_DISK"] == storage.value
    assert values["DB_CONNECTION"] == database.value
    assert literal_value(values["DB_DATABASE"])
    if database == Database.SQLITE:
        assert (tmp_path / literal_value(values["DB_DATABASE"])).parent.is_dir()
    else:
        assert literal_value(values["DB_SERVICE_NAME"]) == "configure-me"
    assert not (tmp_path / "config/database.py").exists()
    assert not (tmp_path / "config/filesystems.py").exists()


@pytest.mark.parametrize("filename", ["database.py", "filesystems.py"])
@pytest.mark.parametrize("source", ["default = (", 'default = "unsupported"'])
def test_existing_mcp_driver_overrides_are_validated(
    tmp_path: Path, filename: str, source: str,
) -> None:
    """Reject broken explicit overrides instead of silently inheriting defaults.

    Parameters
    ----------
    tmp_path : Path
        Isolated application with one invalid configuration override.
    filename : str
        Optional database or filesystem override to validate.
    source : str
        Invalid syntax or configuration missing the selected driver contract.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / filename).write_text(source, encoding="utf-8")
    plan = InstallationPlan(name="mcp-example", path=tmp_path, stack=Stack.SLIM_MCP)

    with pytest.raises(CompatibilityError):
        config_contract(tmp_path, plan)


