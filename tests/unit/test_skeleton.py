"""Verify owned publication and cleanup with disposable offline files."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from orionis_installer.configuration import configure_environment
from orionis_installer.exceptions import (
    Cancelled,
    CompatibilityError,
    ProcessError,
    ValidationError,
)
from orionis_installer.messages import MESSAGES
from orionis_installer.models import STACKS, InstallationPlan, SkeletonSource, Stack
from orionis_installer.skeleton import (
    clone_skeleton,
    publish,
    remove_owned_tree,
    staging_destination,
    validate_tree,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "skeleton"


@pytest.fixture
def template(tmp_path):
    """Copy the identified offline skeleton into a disposable directory.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.

    Returns
    -------
    Path
        Disposable copy of the identified skeleton fixture.
    """
    root = tmp_path / "owned template"
    shutil.copytree(FIXTURE, root)
    return root


def test_template_contract_accepts_identified_offline_fixture(template):
    """Verify template contract accepts identified offline fixture.

    Parameters
    ----------
    template : Path
        Copy of the explicitly identified offline skeleton fixture.
    """
    validate_tree(template)


@pytest.mark.parametrize(
    "missing",
    [
        "pyproject.toml",
        "reactor",
        "bootstrap/app.py",
        "config/database.py",
        "config/filesystems.py",
        ".gitignore",
    ],
)
def test_missing_required_template_file_is_rejected(template, missing):
    """Verify missing required template file is rejected.

    Parameters
    ----------
    template : Path
        Copy of the explicitly identified offline skeleton fixture.
    missing : str
        Required tool or skeleton filename removed for this case.
    """
    (template / missing).unlink()
    with pytest.raises(CompatibilityError, match=re.escape(MESSAGES["skeleton_files_missing"])):
        validate_tree(template)


@pytest.mark.parametrize("operational_file", [".env", ".venv"])
def test_downloaded_template_cannot_contain_operational_environment(template, operational_file):
    """Verify downloaded template cannot contain operational environment.

    Parameters
    ----------
    template : Path
        Copy of the explicitly identified offline skeleton fixture.
    operational_file : str
        Runtime dotenv file or environment directory introduced into staging.
    """
    path = template / operational_file
    if operational_file == ".env":
        path.write_text("secret=value\n", encoding="utf-8")
    else:
        path.mkdir()
    with pytest.raises(CompatibilityError):
        validate_tree(template)


def test_reservation_is_exclusive_and_owned_staging_is_cleaned(tmp_path):
    """Verify reservation is exclusive and owned staging is cleaned.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    destination = tmp_path / "app with spaces"
    lock = tmp_path / ".app with spaces.orionis-install.lock"
    unrelated = tmp_path / "user-notes.txt"
    unrelated.write_text("preserve", encoding="utf-8")
    with staging_destination(destination) as staging:
        assert staging.parent == destination.parent
        assert staging.is_dir()
        assert lock.is_file()
        assert not destination.exists()
        with (
            pytest.raises(ValidationError, match=re.escape(MESSAGES["destination_reserved"])),
            staging_destination(destination),
        ):
            pytest.fail("A second installer obtained the same destination")
        (staging / "owned.txt").write_text("disposable", encoding="utf-8")
    assert not staging.exists()
    assert not lock.exists()
    assert not destination.exists()
    assert unrelated.read_text(encoding="utf-8") == "preserve"


def test_failure_before_publication_removes_only_owned_staging(tmp_path):
    """Verify failure before publication removes only owned staging.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    destination = tmp_path / "app"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "data.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ProcessError), staging_destination(destination) as staging:
        (staging / "partial-download").write_text("partial", encoding="utf-8")
        raise ProcessError("clone failed")
    assert not staging.exists()
    assert (foreign / "data.txt").read_text(encoding="utf-8") == "keep"
    assert not destination.exists()


def test_replaced_reservation_is_preserved(tmp_path):
    """Verify replaced reservation is preserved.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    destination = tmp_path / "app"
    lock = tmp_path / ".app.orionis-install.lock"
    with staging_destination(destination):
        lock.unlink()
        replacement = tmp_path / "replacement-empty-file"
        replacement.touch()
        replacement.rename(lock)
    assert lock.exists(), "Cleanup must preserve a replacement reservation it did not create"


def test_replaced_staging_directory_is_preserved(tmp_path):
    """Verify replaced staging directory is preserved.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    destination = tmp_path / "app"
    moved_owned_staging = tmp_path / "moved-original-staging"
    with (
        pytest.raises(ValidationError, match=re.escape(MESSAGES["cleanup_staging_replaced"])),
        staging_destination(destination) as staging,
    ):
        staging.rename(moved_owned_staging)
        staging.mkdir()
        (staging / "user-file.txt").write_text("foreign replacement", encoding="utf-8")
    assert (staging / "user-file.txt").read_text(encoding="utf-8") == "foreign replacement"
    assert moved_owned_staging.is_dir()
    assert not destination.exists()
    assert not (tmp_path / ".app.orionis-install.lock").exists()


@pytest.mark.parametrize("exception", [Cancelled, KeyboardInterrupt])
def test_cancellation_is_preserved_when_cleanup_refuses_replaced_staging(tmp_path, exception):
    """Verify cancellation is preserved when cleanup refuses replaced staging.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    exception : type[BaseException]
        Cancellation exception injected during staging cleanup.
    """
    destination = tmp_path / "app"
    moved_owned_staging = tmp_path / "moved-original-staging"
    with (
        pytest.raises((exception, Cancelled)) as caught,
        staging_destination(destination) as staging,
    ):
        staging.rename(moved_owned_staging)
        staging.mkdir()
        (staging / "user-file.txt").write_text("foreign replacement", encoding="utf-8")
        raise exception("explicit cancellation")
    if isinstance(caught.value, Cancelled):
        assert caught.value.exit_code == 130
    assert (staging / "user-file.txt").read_text(encoding="utf-8") == "foreign replacement"
    assert not (tmp_path / ".app.orionis-install.lock").exists()


def test_cleanup_rejects_different_directory_identity(tmp_path):
    """Verify cleanup rejects different directory identity.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    owned = tmp_path / "owned"
    owned.mkdir()
    info = owned.lstat()
    identity = (info.st_dev, info.st_ino)
    owned.rename(tmp_path / "original")
    owned.mkdir()
    (owned / "user-file.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValidationError, match=re.escape(MESSAGES["cleanup_staging_replaced"])):
        remove_owned_tree(owned, identity=identity)
    assert (owned / "user-file.txt").read_text(encoding="utf-8") == "keep"


def test_configured_staging_is_published_without_moving_a_virtual_environment(tmp_path):
    """Verify configured staging is published without moving a virtual environment.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    destination = tmp_path / "application with spaces"
    with staging_destination(destination) as staging:
        shutil.copytree(FIXTURE, staging, dirs_exist_ok=True)
        configure_environment(staging, InstallationPlan("app", destination))
        publish(staging, destination)
        assert (destination / ".env").is_file()
        assert not (destination / ".venv").exists()
    assert (destination / "reactor").is_file()
    assert (destination / ".env.example").is_file()
    assert not staging.exists()


def test_racing_destination_creator_is_preserved(template, tmp_path):
    """Verify that a destination created by another process remains intact.

    Parameters
    ----------
    template : Path
        Copy of the explicitly identified offline skeleton fixture.
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    destination = tmp_path / "application"
    destination.mkdir()
    data = destination / "user.txt"
    data.write_text("user created this", encoding="utf-8")
    with pytest.raises(ValidationError):
        publish(template, destination)
    assert data.read_text(encoding="utf-8") == "user created this"
    assert (template / "reactor").exists()


def test_mkdir_race_is_not_overwritten(template, tmp_path, monkeypatch):
    """Verify that exclusive directory creation preserves a competing destination.

    Parameters
    ----------
    template : Path
        Copy of the explicitly identified offline skeleton fixture.
    tmp_path : Path
        Disposable directory supplied by pytest.
    monkeypatch : pytest.MonkeyPatch
        Scoped replacement fixture for controlled dependencies.
    """
    destination = tmp_path / "application"
    actual_mkdir = Path.mkdir

    def racing_mkdir(path, *args, **kwargs):
        """Create foreign destination data during exclusive directory creation.

        Parameters
        ----------
        path : Path
            Filesystem path passed to the controlled replacement operation.
        *args : tuple
            Additional positional arguments forwarded by the fixture.
        **kwargs : dict
            Additional options accepted by the controlled fixture.

        Raises
        ------
        FileExistsError
            If the installer tries to create the competing destination.
        """
        if path == destination:
            actual_mkdir(path)
            (path / "user.txt").write_text("racing-user-data", encoding="utf-8")
            raise FileExistsError(str(path))
        return actual_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", racing_mkdir)
    with pytest.raises(ValidationError, match=re.escape(MESSAGES["destination_race"])):
        publish(template, destination)
    assert (destination / "user.txt").read_text(encoding="utf-8") == "racing-user-data"


def test_partial_publication_preserves_project_on_io_failure(template, tmp_path, monkeypatch):
    """Verify partial publication preserves project on I/O failure.

    Parameters
    ----------
    template : Path
        Copy of the explicitly identified offline skeleton fixture.
    tmp_path : Path
        Disposable directory supplied by pytest.
    monkeypatch : pytest.MonkeyPatch
        Scoped replacement fixture for controlled dependencies.
    """
    destination = tmp_path / "application"
    actual_rename = Path.rename
    moved = []

    def failing_rename(path, target):
        """Fail publication after its first successful file move.

        Parameters
        ----------
        path : Path
            Filesystem path passed to the controlled replacement operation.
        target : Path
            Destination passed to the controlled rename operation.

        Returns
        -------
        Path
            Destination of the first successful move.

        Raises
        ------
        OSError
            If a later move is attempted.
        """
        if moved:
            raise OSError("publication interrupted")
        moved.append(path.name)
        return actual_rename(path, target)

    monkeypatch.setattr(Path, "rename", failing_rename)
    with pytest.raises(OSError):
        publish(template, destination)
    assert destination.is_dir()
    assert (destination / moved[0]).exists()


def test_publication_rejects_a_replaced_destination_pointer(template, tmp_path):
    """Verify publication rejects a replaced destination pointer.

    Parameters
    ----------
    template : Path
        Copy of the explicitly identified offline skeleton fixture.
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    destination = tmp_path / "application"
    original = tmp_path / "original-created-directory"
    foreign = tmp_path / "foreign-directory"
    foreign.mkdir()
    marker = foreign / "user-data.txt"
    marker.write_text("preserve foreign data", encoding="utf-8")
    junction = False

    def replace_created_directory():
        """Replace the created destination with a link to foreign fixture data."""
        nonlocal junction
        destination.rename(original)
        try:
            destination.symlink_to(foreign, target_is_directory=True)
        except OSError as exc:
            if os.name != "nt":
                pytest.skip(f"OS does not permit symlink creation: {exc}")
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(destination), str(foreign)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode:
                pytest.skip("Windows did not permit a disposable junction")
            junction = True

    try:
        with pytest.raises(ValidationError):
            publish(template, destination, on_created=replace_created_directory)
        assert sorted(p.name for p in foreign.iterdir()) == ["user-data.txt"]
        assert marker.read_text(encoding="utf-8") == "preserve foreign data"
        assert (template / "reactor").is_file()
    finally:
        if os.path.lexists(destination):
            if junction:
                destination.rmdir()
            elif destination.is_symlink():
                destination.unlink()


def test_readonly_git_files_are_removed_without_affecting_siblings(tmp_path):
    """Verify that cleanup removes read-only Git files and preserves siblings.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    owned = tmp_path / "owned-git"
    owned.mkdir()
    readonly = owned / "readonly-index"
    readonly.write_text("index", encoding="utf-8")
    readonly.chmod(stat.S_IREAD)
    foreign = tmp_path / "user-file"
    foreign.write_text("keep", encoding="utf-8")
    remove_owned_tree(owned)
    assert not owned.exists()
    assert foreign.read_text(encoding="utf-8") == "keep"


def test_symlinks_are_rejected_and_foreign_target_is_preserved(template, tmp_path):
    """Verify symlinks are rejected and foreign target is preserved.

    Parameters
    ----------
    template : Path
        Copy of the explicitly identified offline skeleton fixture.
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    foreign = tmp_path / "foreign-data"
    foreign.mkdir()
    data = foreign / "data.txt"
    data.write_text("important", encoding="utf-8")
    link = template / "linked-data"
    try:
        link.symlink_to(foreign, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"OS does not permit symlink creation: {exc}")
    try:
        with pytest.raises(CompatibilityError):
            validate_tree(template)
        with pytest.raises(ValidationError):
            remove_owned_tree(template)
        assert data.read_text(encoding="utf-8") == "important"
    finally:
        link.unlink()


class OfflineCloneRunner:
    """Inject explicit offline clone responses through the process seam."""

    def __init__(self, revision="a" * 40, failure=False, head=None):
        """Initialize controlled clone responses and captured process calls.

        Parameters
        ----------
        revision : str, optional
            Simulated clone revision reported by Git.
        failure : bool, optional
            Simulate a clone process failure.
        head : str or None, optional
            Reported symbolic HEAD, inferred from the clone branch when omitted.
            An empty string simulates the process failure for detached HEAD.
        """
        self.calls = []
        self.revision = revision
        self.failure = failure
        self.head = head
        self.cloned_branch = None

    def run(self, arguments, *, cwd, timeout):
        """Simulate a clone, symbolic branch check, or provenance revision lookup.

        Parameters
        ----------
        arguments : Sequence[str or Path]
            Separate command arguments supplied to the clone fixture.
        cwd : Path
            Explicit working directory recorded by the process fixture.
        timeout : float, optional
            Execution limit recorded by the process fixture.

        Returns
        -------
        SimpleNamespace
            Empty clone output, the selected branch, or configured revision in stdout.

        Raises
        ------
        ProcessError
            If cloning should fail or the simulated HEAD is detached.
        """
        self.calls.append((arguments, cwd, timeout))
        if "clone" in arguments:
            if self.failure:
                raise ProcessError("Offline injected clone failure")
            self.cloned_branch = arguments[arguments.index("--branch") + 1]
            staging = Path(arguments[-1])
            shutil.copytree(FIXTURE, staging, dirs_exist_ok=True)
            (staging / ".git").mkdir()
            (staging / ".git" / "index").write_text("fixture index", encoding="utf-8")
            return SimpleNamespace(stdout="")
        if arguments[1] == "symbolic-ref":
            if self.head == "":
                raise ProcessError("Offline detached HEAD fixture")
            head = self.head if self.head is not None else f"refs/heads/{self.cloned_branch}"
            return SimpleNamespace(stdout=head + "\n")
        return SimpleNamespace(stdout=self.revision + "\n")


@pytest.mark.parametrize("stack", list(Stack))
def test_clone_uses_selected_source_and_removes_only_git(tmp_path, stack):
    """Verify that cloning selects the exact stack branch and removes only Git metadata.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    stack : Stack
        Catalog entry whose repository and exact branch should be cloned.
    """
    staging = tmp_path / "staging with spaces"
    staging.mkdir()
    runner = OfflineCloneRunner()
    source = STACKS[stack]
    assert clone_skeleton(staging, Path("trusted-git"), runner, source=source) == "a" * 40
    arguments, cwd, timeout = runner.calls[0]
    assert arguments == [
        Path("trusted-git"),
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--branch",
        source.branch,
        "--no-recurse-submodules",
        source.repository,
        staging,
    ]
    assert cwd == staging.parent and timeout == 180
    assert runner.calls[1] == (
        [Path("trusted-git"), "symbolic-ref", "--quiet", "HEAD"],
        staging,
        15,
    )
    assert runner.calls[2][0] == [Path("trusted-git"), "rev-parse", "HEAD"]
    assert not (staging / ".git").exists()
    assert (staging / ".gitignore").is_file()
    assert (staging / "LICENCE").read_text(encoding="utf-8").startswith("Synthetic fixture")
    assert (staging / "README.md").is_file()


def test_clone_honors_repository_and_branch_from_source_metadata(tmp_path):
    """Verify that catalog metadata controls both the repository and the branch.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory containing the simulated clone destination.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    source = SkeletonSource(
        "https://example.test/custom-template", "release/custom", "Custom", "Fixture source"
    )
    runner = OfflineCloneRunner()
    clone_skeleton(staging, Path("trusted-git"), runner, source=source)
    arguments = runner.calls[0][0]
    assert arguments[arguments.index("--branch") + 1] == source.branch
    assert arguments[-2] == source.repository


@pytest.mark.parametrize("stack", list(Stack))
@pytest.mark.parametrize("head", ["", "refs/heads/main", "refs/tags/blank_1.x"])
def test_clone_rejects_detached_or_different_branch_before_recording_revision(
    tmp_path, stack, head
):
    """Reject tag checkouts and incorrect branches before reading the provenance SHA.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory containing the controlled clone destination.
    stack : Stack
        Exact selected source branch required for provenance.
    head : str
        Detached or incorrect symbolic reference returned by the Git fixture.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    runner = OfflineCloneRunner(head=head)
    with pytest.raises(CompatibilityError, match="selected Git branch"):
        clone_skeleton(staging, Path("trusted-git"), runner, source=STACKS[stack])
    assert not any("rev-parse" in arguments for arguments, _cwd, _timeout in runner.calls)
    assert (staging / ".git").is_dir()


@pytest.mark.parametrize("revision", ["", "not-sha", "g" * 40, "a" * 39, "a" * 41])
def test_unverifiable_clone_revision_fails(tmp_path, revision):
    """Verify unverifiable clone revision fails.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    revision : str, optional
        Simulated clone revision reported by Git.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(CompatibilityError, match="SHA"):
        clone_skeleton(staging, Path("trusted-git"), OfflineCloneRunner(revision=revision))


def test_clone_process_failure_leaves_no_destination(tmp_path):
    """Verify that a failed clone leaves no published destination.

    Parameters
    ----------
    tmp_path : Path
        Disposable directory supplied by pytest.
    """
    destination = tmp_path / "application"
    with pytest.raises(ProcessError), staging_destination(destination) as staging:
        clone_skeleton(staging, Path("trusted-git"), OfflineCloneRunner(failure=True))
    assert not staging.exists()
    assert not destination.exists()
