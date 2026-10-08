from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit
from orionis_installer.exceptions import ValidationError
from orionis_installer.messages import MESSAGES

DEFAULT_DESCRIPTION = "A modern application built with Orionis Framework."

class Stack(StrEnum):
    """Enumerate the application stacks available in the source catalog."""

    BLANK = "blank"
    SSR = "ssr"

@dataclass(frozen=True)
class SkeletonSource:
    """
    Describe a stack's repository, exact branch, and presentation metadata.

    Attributes
    ----------
    repository : str
        Repository cloned to create an application with this stack.
    branch : str
        Exact branch to clone, without falling back to another branch.
    label : str
        Human-readable stack name presented during installation.
    description : str
        Short explanation of the starting application provided by this stack.
    """

    repository: str
    branch: str
    label: str
    description: str

    def __post_init__(self) -> None:
        """
        Reject catalog mistakes before any repository is downloaded.

        Raises
        ------
        ValidationError
            If metadata is empty or contains controls, the HTTPS URL includes
            credentials, or the branch violates Git's explicit reference syntax.
        """
        metadata = (self.repository, self.branch, self.label, self.description)
        if any(
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or any(ord(character) < 32 or 127 <= ord(character) < 160 for character in value)
            for value in metadata
        ):
            raise ValidationError(MESSAGES["skeleton_source_invalid"])
        try:
            repository = urlsplit(self.repository)
            port = repository.port
            valid_repository = (
                repository.scheme == "https"
                and bool(repository.hostname)
                and repository.username is None
                and repository.password is None
                and (port is None or port > 0)
                and not repository.query
                and not repository.fragment
                and "\\" not in self.repository
                and not any(character.isspace() for character in self.repository)
            )
        except ValueError:
            valid_repository = False
        invalid_branch = (
            self.branch in {"@", "HEAD"}
            or self.branch.startswith(("-", "/"))
            or self.branch.endswith(("/", "."))
            or any(token in self.branch for token in ("..", "@{", "//"))
            or any(character in "~^:?*[\\" or character.isspace() for character in self.branch)
            or any(
                component.startswith(".") or component.endswith(".lock")
                for component in self.branch.split("/")
            )
        )
        if not valid_repository or invalid_branch:
            raise ValidationError(MESSAGES["skeleton_source_invalid"])

STACKS: dict[Stack, SkeletonSource] = {
    Stack.BLANK: SkeletonSource(
        repository="https://github.com/orionis-framework/skeleton",
        branch="blank_1.x",
        label="Blank",
        description="A minimal foundation for building your application from scratch.",
    ),
    Stack.SSR: SkeletonSource(
        repository="https://github.com/orionis-framework/skeleton",
        branch="ssr_1.x",
        label="SSR",
        description="A starting point for applications with server-side rendering.",
    ),
}
DEFAULT_STACK = Stack.BLANK

class Storage(StrEnum):
    """Enumerate supported storage choices and the aggregate SDK selection."""

    LOCAL = "local"
    S3 = "s3"
    AZURE = "azure"
    GCS = "gcs"
    ALL = "all"

class Database(StrEnum):
    """Enumerate supported database choices and the aggregate driver selection."""

    SQLITE = "sqlite"
    MYSQL = "mysql"
    PGSQL = "pgsql"
    ORACLE = "oracle"
    SQLSERVER = "sqlserver"
    REDSHIFT = "redshift"
    ALL = "all"

class State(StrEnum):
    """Represent the observable state of each installation operation."""

    PENDING = MESSAGES["state_pending"]
    RUNNING = MESSAGES["state_running"]
    COMPLETED = MESSAGES["state_completed"]
    SKIPPED = MESSAGES["state_skipped"]
    FAILED = MESSAGES["state_failed"]
    CANCELLED = MESSAGES["state_cancelled"]

@dataclass(frozen=True)
class InstallationPlan:
    """
    Describe immutable application metadata and validated driver selections.

    Attributes
    ----------
    name : str
        Portable application name used for its directory and manifest.
    path : Path
        Absolute destination directory for the application.
    description : str
        Application description written to the manifest.
    author_name : str or None
        Optional author name included in project metadata.
    author_email : str or None
        Optional validated author email included in project metadata.
    storage : Storage
        Requested storage SDK selection.
    database : Database
        Requested database driver selection.
    default_storage : Storage or None
        Effective disk when installing all storage SDKs.
    default_database : Database or None
        Effective connection when installing all database drivers.
    stack : Stack
        Source catalog entry defining the starting application's repository and branch.
    """

    name: str
    path: Path
    description: str = DEFAULT_DESCRIPTION
    author_name: str | None = None
    author_email: str | None = None
    storage: Storage = Storage.LOCAL
    database: Database = Database.SQLITE
    default_storage: Storage | None = None
    default_database: Database | None = None
    stack: Stack = DEFAULT_STACK

    def __post_init__(self) -> None:
        """
        Validate metadata and driver defaults, then normalize the destination.

        Raises
        ------
        ValidationError
            If metadata is invalid, the stack or drivers are unsupported, or defaults conflict.
        """
        from orionis_installer.validation import validate_email, validate_name, validate_text

        if not isinstance(self.stack, Stack) or self.stack not in STACKS:
            raise ValidationError(MESSAGES["plan_stack_invalid"])
        if not isinstance(self.storage, Storage) or not isinstance(self.database, Database):
            raise ValidationError(MESSAGES["plan_drivers_invalid"])
        validate_name(self.name)
        validate_text(self.description, MESSAGES["description_label"])
        if self.author_name:
            validate_text(self.author_name, MESSAGES["author_label"])
        if self.author_email:
            validate_email(self.author_email)
        if any(
            default is not None and not isinstance(default, kind)
            for default, kind in (
                (self.default_storage, Storage), (self.default_database, Database),
            )
        ):
            raise ValidationError(MESSAGES["default_driver_invalid"])
        if self.default_storage == Storage.ALL or self.default_database == Database.ALL:
            raise ValidationError(MESSAGES["default_driver_invalid"])
        if self.storage != Storage.ALL and self.default_storage not in (None, self.storage):
            raise ValidationError(MESSAGES["default_storage_invalid"])
        if self.database != Database.ALL and self.default_database not in (None, self.database):
            raise ValidationError(MESSAGES["default_database_invalid"])
        validate_text(str(self.path), MESSAGES["destination_label"])
        object.__setattr__(self, "path", self.path.absolute())

    @property
    def source(self) -> SkeletonSource:
        """
        Resolve the selected stack's repository and branch from the catalog.

        Returns
        -------
        SkeletonSource
            Immutable source metadata for the validated stack selection.
        """
        return STACKS[self.stack]

    @property
    def active_storage(self) -> Storage:
        """
        Resolve the concrete storage disk used by the application.

        Returns
        -------
        Storage
            Explicit default, selected disk, or local disk for the aggregate choice.
        """
        return self.default_storage or (
            Storage.LOCAL if self.storage == Storage.ALL else self.storage
        )

    @property
    def active_database(self) -> Database:
        """
        Resolve the concrete database connection used by the application.

        Returns
        -------
        Database
            Explicit default, selected connection, or SQLite for the aggregate choice.
        """
        return self.default_database or (
            Database.SQLITE if self.database == Database.ALL else self.database
        )

    @property
    def extras(self) -> tuple[str, ...]:
        """
        Build deterministic Orionis extras for factories and selected drivers.

        Returns
        -------
        tuple[str, ...]
            Sorted, unique extras using aggregate names for all-driver selections.
        """
        extras = {"factories"}
        if self.storage != Storage.LOCAL:
            extras.add("storage" if self.storage == Storage.ALL else self.storage.value)
        if self.database != Database.SQLITE:
            extras.add("database" if self.database == Database.ALL else self.database.value)
        return tuple(sorted(extras))

@dataclass(frozen=True)
class PostInstallOptions:
    """
    Preserve explicit post-install choices independently of prompt defaults.

    Attributes
    ----------
    git : bool or None
        Whether to initialize Git, or None to defer to interaction policy.
    migrate : bool or None
        Whether to request migrations, or None to defer to interaction policy.
    open : bool or None
        Whether to open the editor, or None to defer to interaction policy.
    """

    git: bool | None = None
    migrate: bool | None = None
    open: bool | None = None

@dataclass
class InstallationResult:
    """
    Track creation, independent follow-up operations, and diagnostic warnings.

    Attributes
    ----------
    plan : InstallationPlan
        Application metadata and destination for this installation.
    creation : State
        Current application creation state.
    git : State
        Current Git initialization state.
    migrations : State
        Current schema migration state.
    editor : State
        Current editor launch state.
    python_version : str or None
        Verified application interpreter version.
    framework_version : str or None
        Verified installed Orionis version.
    warnings : list[str]
        Diagnostics scoped to this installation result.
    published : bool
        Whether the staged application reached its final destination.
    """

    plan: InstallationPlan
    creation: State = State.PENDING
    git: State = State.PENDING
    migrations: State = State.PENDING
    editor: State = State.PENDING
    python_version: str | None = None
    framework_version: str | None = None
    warnings: list[str] = field(default_factory=list)
    published: bool = False

    @property
    def exit_code(self) -> int:
        """
        Derive the process exit code from creation and follow-up states.

        Returns
        -------
        int
            Zero for success, one for creation failure, three for follow-up failure,
            or 130 for cancellation.
        """
        if State.CANCELLED in (self.creation, self.git, self.migrations, self.editor):
            return 130
        if self.creation != State.COMPLETED:
            return 1
        return 3 if State.FAILED in (self.git, self.migrations, self.editor) else 0
