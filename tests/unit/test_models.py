"""Installation choices are pure and do not require a terminal or Orionis."""

from itertools import product
from pathlib import Path

import pytest

from orionis_installer.exceptions import ValidationError
from orionis_installer.models import (
    Database,
    InstallationPlan,
    InstallationResult,
    PostInstallOptions,
    State,
    Storage,
)


@pytest.mark.parametrize(("storage", "database"), list(product(Storage, Database)))
def test_every_storage_database_combination(storage, database, tmp_path):
    """Verify extras and effective defaults for every storage and database combination.

    Parameters
    ----------
    storage : Storage
        Storage SDK selection exercised by the parameterized case.
    database : Database
        Database driver selection exercised by the parameterized case.
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    plan = InstallationPlan(
        "example-app", tmp_path / "application", storage=storage, database=database
    )
    expected = {"factories"}
    if storage != Storage.LOCAL:
        expected.add("storage" if storage == Storage.ALL else storage.value)
    if database != Database.SQLITE:
        expected.add("database" if database == Database.ALL else database.value)
    assert plan.extras == tuple(sorted(expected))
    assert len(plan.extras) == len(set(plan.extras))
    assert plan.active_storage == (Storage.LOCAL if storage == Storage.ALL else storage)
    assert plan.active_database == (Database.SQLITE if database == Database.ALL else database)
    assert plan.active_storage != Storage.ALL
    assert plan.active_database != Database.ALL


@pytest.mark.parametrize(
    ("storage", "database", "expected"),
    [
        (Storage.LOCAL, Database.SQLITE, ("factories",)),
        (Storage.S3, Database.PGSQL, ("factories", "pgsql", "s3")),
        (Storage.AZURE, Database.MYSQL, ("azure", "factories", "mysql")),
        (Storage.S3, Database.REDSHIFT, ("factories", "redshift", "s3")),
        (Storage.ALL, Database.ALL, ("database", "factories", "storage")),
    ],
)
def test_product_examples(storage, database, expected, tmp_path):
    """Verify dependency extras for representative combinations of product choices.

    Parameters
    ----------
    storage : Storage
        Storage SDK selection exercised by the parameterized case.
    database : Database
        Database driver selection exercised by the parameterized case.
    expected : tuple[str, ...]
        Expected sorted Orionis extras for the selected drivers.
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    assert (
        InstallationPlan("blog", tmp_path / "blog", storage=storage, database=database).extras
        == expected
    )


@pytest.mark.parametrize("storage", [value for value in Storage if value != Storage.ALL])
@pytest.mark.parametrize("database", [value for value in Database if value != Database.ALL])
def test_all_drivers_have_one_explicit_default(storage, database, tmp_path):
    """Verify that all drivers have one explicit default.

    Parameters
    ----------
    storage : Storage
        Storage SDK selection exercised by the parameterized case.
    database : Database
        Database driver selection exercised by the parameterized case.
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    plan = InstallationPlan(
        "blog",
        tmp_path / "blog",
        storage=Storage.ALL,
        database=Database.ALL,
        default_storage=storage,
        default_database=database,
    )
    assert plan.active_storage == storage
    assert plan.active_database == database
    assert plan.extras == ("database", "factories", "storage")


@pytest.mark.parametrize(
    "choices",
    [
        {"default_storage": Storage.ALL},
        {"default_database": Database.ALL},
        {"storage": Storage.S3, "default_storage": Storage.AZURE},
        {"database": Database.MYSQL, "default_database": Database.PGSQL},
    ],
)
def test_conflicting_defaults_are_rejected(choices, tmp_path):
    """Verify that conflicting defaults are rejected.

    Parameters
    ----------
    choices : dict
        Driver selections and defaults supplied to the installation plan.
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    with pytest.raises(ValidationError):
        InstallationPlan("blog", tmp_path / "blog", **choices)


def test_optional_authors_and_unicode_text(tmp_path):
    """Verify that optional authors and Unicode metadata remain valid.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    plan = InstallationPlan("blog", tmp_path / "blog", description="An application in Montréal 🐍")
    assert plan.author_name is None
    assert plan.author_email is None
    assert plan.path.is_absolute()
    author = InstallationPlan("blog", tmp_path / "blog", author_name="Renée O'Connor")
    assert author.author_email is None


def test_unset_post_options_are_distinct_from_negative_flags():
    """Verify that unset follow-up options remain distinct from explicit negative flags."""
    assert PostInstallOptions().git is None
    assert PostInstallOptions(git=False).git is False
    assert PostInstallOptions(migrate=False).migrate is False
    assert PostInstallOptions(open=False).open is False


@pytest.mark.parametrize(
    ("creation", "post_state", "expected"),
    [
        (State.PENDING, State.SKIPPED, 1),
        (State.FAILED, State.SKIPPED, 1),
        (State.CANCELLED, State.SKIPPED, 130),
        (State.COMPLETED, State.SKIPPED, 0),
        (State.COMPLETED, State.COMPLETED, 0),
        (State.COMPLETED, State.FAILED, 3),
    ],
)
def test_result_exit_codes(creation, post_state, expected):
    """Verify exit codes derived from creation and follow-up operation states.

    Parameters
    ----------
    creation : State
        Application creation state used to derive the exit code.
    post_state : State
        Follow-up operation state used to derive the exit code.
    expected : int
        Expected exit code for the supplied operation states.
    """
    result = InstallationResult(
        InstallationPlan("blog", Path("blog")), creation=creation, git=post_state
    )
    assert result.exit_code == expected


def test_result_warning_lists_do_not_leak_between_installations(tmp_path):
    """Verify that result warning lists do not leak between installations.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory isolating filesystem changes for this test.
    """
    plan = InstallationPlan("blog", tmp_path / "blog")
    first, second = InstallationResult(plan), InstallationResult(plan)
    first.warnings.append("recover with uv sync")
    assert second.warnings == []
