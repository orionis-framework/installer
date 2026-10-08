import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from orionis_installer.exceptions import (
    Cancelled,
    CompatibilityError,
    ProcessError,
    ValidationError,
)
from orionis_installer.messages import MESSAGES
from orionis_installer.models import DEFAULT_STACK, STACKS, SkeletonSource
from orionis_installer.validation import is_redirect, validate_destination

def validate_tree(root: Path, *, configured: bool = False) -> None:
    """
    Validate required skeleton files and reject filesystem redirections.

    Parameters
    ----------
    root : Path
        Owned clone or configured staging directory.
    configured : bool, optional
        Allow the generated dotenv file after configuration.

    Raises
    ------
    CompatibilityError
        If required files are absent or runtime files and redirects are unsafe.
    """
    if is_redirect(root):
        raise CompatibilityError(MESSAGES["staging_redirect"])
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            if is_redirect(Path(current) / name):
                raise CompatibilityError(MESSAGES["skeleton_redirect"])
    required = (
        "pyproject.toml",
        "reactor",
        "bootstrap/app.py",
        "config/database.py",
        "config/filesystems.py",
        ".gitignore",
    )
    if any(not (root / name).is_file() for name in required):
        raise CompatibilityError(MESSAGES["skeleton_files_missing"])
    if (root / ".venv").exists() or (not configured and (root / ".env").exists()):
        raise CompatibilityError(MESSAGES["skeleton_runtime_distributed"])

def _remove_readonly(function: object, path: str, error: BaseException) -> None:  # NOSONAR
    """
    Retry an owned-tree deletion after removing a read-only file flag.

    Parameters
    ----------
    function : object
        Deletion callback supplied by shutil.rmtree.
    path : str
        Owned file whose permissions blocked deletion.
    error : BaseException
        Original deletion error.

    Raises
    ------
    BaseException
        If the original error was unrelated to file permissions.
    """
    # Called only by rmtree on a previously validated, owned directory.
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    if callable(function):
        function(path) # NOSONAR

def remove_owned_tree(root: Path, *, identity: tuple[int, int] | None = None) -> None:
    """
    Remove an owned tree after verifying identity and redirect safety.

    Parameters
    ----------
    root : Path
        Owned directory to remove if it still exists.
    identity : tuple[int, int] or None, optional
        Expected device and inode pair from directory creation.

    Raises
    ------
    ValidationError
        If the directory was replaced or contains filesystem redirects.
    OSError
        If an owned file cannot be removed safely.
    """
    if not root.exists():
        return
    if is_redirect(root):
        raise ValidationError(MESSAGES["cleanup_staging_redirect"])
    info = root.lstat()
    if identity is not None and (info.st_dev, info.st_ino) != identity:
        raise ValidationError(MESSAGES["cleanup_staging_replaced"])
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            node = Path(current) / name
            if is_redirect(node):
                raise ValidationError(MESSAGES["cleanup_tree_redirect"])
    shutil.rmtree(root, onexc=_remove_readonly)

@contextmanager
def staging_destination(destination: Path) -> Iterator[Path]:
    """
    Reserve a destination and yield its exclusively owned sibling staging.

    Parameters
    ----------
    destination : Path
        Final application directory, which must not exist.

    Yields
    ------
    Path
        Temporary sibling directory cleaned only while its identity is intact.

    Raises
    ------
    ValidationError
        If reservation, destination validation or safe cleanup is refused.
    Cancelled
        If cancellation coincides with refused staging cleanup.
    OSError
        If reservation or controlled filesystem operations fail.
    """
    validate_destination(destination)
    lock = destination.parent / f".{destination.name}.orionis-install.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValidationError(MESSAGES["destination_reserved"]) from exc
    staging: Path | None = None
    staging_identity: tuple[int, int] | None = None
    lock_identity = os.fstat(descriptor)
    active_error: BaseException | None = None
    try:
        os.close(descriptor)
        # Recheck under reservation, including an unrelated creator racing us.
        validate_destination(destination)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.orionis-", dir=destination.parent)
        )
        info = staging.lstat()
        staging_identity = (info.st_dev, info.st_ino)
        yield staging
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        try:
            try:
                if staging is not None:
                    remove_owned_tree(staging, identity=staging_identity)
            finally:
                # Release our reservation even when unsafe staging must be preserved.
                if (
                    lock.exists()
                    and not is_redirect(lock)
                    and lock.stat().st_ino == lock_identity.st_ino
                    and lock.stat().st_dev == lock_identity.st_dev
                ):
                    lock.unlink()
        except (ValidationError, OSError) as cleanup_error:
            if isinstance(active_error, (Cancelled, KeyboardInterrupt)):
                cancellation = (
                    str(active_error)
                    if isinstance(active_error, Cancelled)
                    else MESSAGES["installation_cancelled"]
                )
                diagnostic = (
                    str(cleanup_error)
                    if isinstance(cleanup_error, ValidationError)
                    else MESSAGES["installation_files_failed"]
                )
                raise Cancelled(
                    MESSAGES["cancelled_cleanup_refused"].format(
                        cancellation=cancellation, diagnostic=diagnostic, staging=staging
                    )
                ) from None
            raise

def publish(
    staging: Path, destination: Path, *, on_created: Callable[[], None] | None = None
) -> None:
    """
    Publish configured staging into an exclusively created final directory.

    Parameters
    ----------
    staging : Path
        Validated owned directory whose content will be moved.
    destination : Path
        Final application path, which must not already exist.
    on_created : Callable[[], None] or None, optional
        Callback invoked after the final directory is created.

    Raises
    ------
    ValidationError
        If the final path appears concurrently or a controlled path is replaced.
    CompatibilityError
        If staging fails the skeleton or redirect contract.
    OSError
        If publication fails; already published files remain recoverable.
    """
    validate_destination(destination)
    validate_tree(staging, configured=True)
    try:
        destination.mkdir()
    except FileExistsError as exc:
        raise ValidationError(MESSAGES["destination_race"]) from exc
    info = destination.lstat()
    identity = (info.st_dev, info.st_ino)
    if on_created:
        on_created()
    # Publication may be partial on I/O failure. Preserve the exclusively created directory.
    for child in staging.iterdir():
        info = destination.lstat()
        if is_redirect(destination) or (info.st_dev, info.st_ino) != identity:
            raise ValidationError(MESSAGES["publication_destination_replaced"])
        for ancestor in (destination.parent, *destination.parent.parents):
            if is_redirect(ancestor):
                raise ValidationError(MESSAGES["publication_ancestor_redirect"])
        if is_redirect(child):
            raise CompatibilityError(MESSAGES["publication_staging_redirect"])
        child.rename(destination / child.name)

def clone_skeleton(
    staging: Path, git: Path, runner: object, *, source: SkeletonSource | None = None
) -> str:
    """
    Clone the selected stack's exact branch and preserve its provenance SHA.

    Parameters
    ----------
    staging : Path
        Empty owned sibling directory receiving the clone.
    git : Path
        Trusted native Git executable.
    runner : object
        Process component exposing the isolated run interface.
    source : SkeletonSource or None, optional
        Catalog source to clone, defaulting to the Blank stack.

    Returns
    -------
    str
        Verified 40-character SHA captured before removing the clone's .git.

    Raises
    ------
    CompatibilityError
        If the downloaded tree, checked-out branch, or revision violates the contract.
    ProcessError
        If cloning the selected branch or reading its revision fails.
    """
    source = STACKS[DEFAULT_STACK] if source is None else source
    runner.run(  # type: ignore[attr-defined]
        [
            git,
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "--branch",
            source.branch,
            "--no-recurse-submodules",
            source.repository,
            staging,
        ],
        cwd=staging.parent,
        timeout=180,
    )
    validate_tree(staging)
    # Git's --branch also accepts tags. Require the selected local branch before
    # recording provenance, because a tag checkout leaves HEAD detached.
    try:
        branch = runner.run(  # type: ignore[attr-defined]
            [git, "symbolic-ref", "--quiet", "HEAD"], cwd=staging, timeout=15
        ).stdout.strip()
    except ProcessError as exc:
        raise CompatibilityError(
            MESSAGES["source_branch_invalid"].format(branch=source.branch)
        ) from exc
    if branch != f"refs/heads/{source.branch}":
        raise CompatibilityError(MESSAGES["source_branch_invalid"].format(branch=source.branch))
    revision = runner.run(  # type: ignore[attr-defined]
        [git, "rev-parse", "HEAD"], cwd=staging, timeout=15
    ).stdout.strip()
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise CompatibilityError(MESSAGES["skeleton_revision_invalid"])
    remove_owned_tree(staging / ".git")
    return revision
