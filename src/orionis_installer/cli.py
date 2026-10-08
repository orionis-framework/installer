from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
import typer
from orionis_installer import __version__
from orionis_installer.exceptions import Cancelled, InstallerError
from orionis_installer.installer import Installer
from orionis_installer.models import (
    DEFAULT_DESCRIPTION,
    DEFAULT_STACK,
    STACKS,
    Database,
    InstallationPlan,
    PostInstallOptions,
    Stack,
    Storage,
)
from orionis_installer.post_install import run_post_install
from orionis_installer.prerequisites import check_prerequisites
from orionis_installer.processes import Runner
from orionis_installer.ui import UI
from orionis_installer.ui.messages import (
    CLI_HELP,
    DATABASE_CHOICES,
    MESSAGES,
    STACK_CHOICES,
    STORAGE_CHOICES,
)
from orionis_installer.validation import (
    validate_destination,
    validate_email,
    validate_name,
    validate_text,
)

app = typer.Typer(
    name="orionis",
    help=CLI_HELP["app"],
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)


def show_version(value: bool) -> None:
    """
    Print the installer version when the version flag is enabled.

    Parameters
    ----------
    value : bool
        Flag indicating whether version information was requested.

    Returns
    -------
    None
        Return without output when the version flag is disabled.

    Raises
    ------
    typer.Exit
        After printing the version when ``value`` is true.
    """
    if value:
        typer.echo(f"orionis-installer {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    context: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=show_version,
            is_eager=True,
            help=CLI_HELP["version"],
        ),
    ] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help=CLI_HELP["no_color"])] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help=CLI_HELP["verbose"]),
    ] = False,
) -> None:
    """
    Store global color and verbosity preferences in the command context.

    Parameters
    ----------
    context : typer.Context
        Context whose object stores preferences for the selected command.
    version : bool, optional
        Version flag handled eagerly by ``show_version``.
    no_color : bool, optional
        Disable terminal colors for subsequent commands.
    verbose : bool, optional
        Enable additional safe diagnostics for subsequent commands.

    Returns
    -------
    None
        Initialize the context object and store the terminal preferences.
    """
    context.ensure_object(dict)
    context.obj.update(no_color=no_color, verbose=verbose)


@dataclass
class _NewCommand:
    """
    Store CLI options and execute application creation after parsing.

    Parameters
    ----------
    context : typer.Context
        Context containing global terminal preferences.
    name : str or None, optional
        Application name; omission prompts or uses ``orionis-app``.
    stack : Stack or None, optional
        Skeleton source; omission prompts or uses ``DEFAULT_STACK``.
    path : Path or None, optional
        Project destination; omission uses the named folder in the current directory.
    description : str or None, optional
        Project description; omission prompts or uses ``DEFAULT_DESCRIPTION``.
    author_name : str or None, optional
        Author name; omission may prompt, and an empty value excludes the field.
    author_email : str or None, optional
        Author email; omission may prompt, and an empty value excludes the field.
    storage : Storage or None, optional
        Storage drivers; omission prompts or uses ``Storage.LOCAL``.
    database : Database or None, optional
        Database drivers; omission prompts or uses ``Database.SQLITE``.
    default_storage : Storage or None, optional
        Active disk when ``storage`` is ``Storage.ALL``.
    default_database : Database or None, optional
        Active connection when ``database`` is ``Database.ALL``.
    git : bool or None, optional
        Git initialization consent; ``None`` defers to post-install handling.
    migrate : bool or None, optional
        Migration consent; ``None`` defers to post-install handling.
    open_editor : bool or None, optional
        Editor launch consent; ``None`` defers to post-install handling.
    no_interaction : bool, optional
        Skip all prompts and use safe defaults.
    no_color : bool, optional
        Disable color in addition to any global monochrome preference.
    verbose : bool, optional
        Display safe diagnostics in addition to any global verbosity setting.

    Raises
    ------
    typer.Exit
        On completion, cancellation, or a handled validation or installation failure.
    """

    context: typer.Context
    name: Annotated[
        str | None,
        typer.Argument(help=CLI_HELP["name"]),
    ] = None
    stack: Annotated[
        Stack | None,
        typer.Option(
            "--stack", case_sensitive=False, help=CLI_HELP["stack"], rich_help_panel="Application"
        ),
    ] = None
    path: Annotated[
        Path | None,
        typer.Option("--path", help=CLI_HELP["path"], rich_help_panel="Application"),
    ] = None
    description: Annotated[
        str | None,
        typer.Option("--description", help=CLI_HELP["description"], rich_help_panel="Application"),
    ] = None
    author_name: Annotated[
        str | None,
        typer.Option("--author-name", help=CLI_HELP["author_name"], rich_help_panel="Application"),
    ] = None
    author_email: Annotated[
        str | None,
        typer.Option(
            "--author-email", help=CLI_HELP["author_email"], rich_help_panel="Application"
        ),
    ] = None
    storage: Annotated[
        Storage | None,
        typer.Option("--storage", help=CLI_HELP["storage"], rich_help_panel="Services"),
    ] = None
    database: Annotated[
        Database | None,
        typer.Option("--database", help=CLI_HELP["database"], rich_help_panel="Services"),
    ] = None
    default_storage: Annotated[
        Storage | None,
        typer.Option(
            "--default-storage", help=CLI_HELP["default_storage"], rich_help_panel="Services"
        ),
    ] = None
    default_database: Annotated[
        Database | None,
        typer.Option(
            "--default-database", help=CLI_HELP["default_database"], rich_help_panel="Services"
        ),
    ] = None
    git: Annotated[
        bool | None,
        typer.Option("--git/--no-git", help=CLI_HELP["git"], rich_help_panel="Project setup"),
    ] = None
    migrate: Annotated[
        bool | None,
        typer.Option(
            "--migrate/--no-migrate", help=CLI_HELP["migrate"], rich_help_panel="Project setup"
        ),
    ] = None
    open_editor: Annotated[
        bool | None,
        typer.Option("--open/--no-open", help=CLI_HELP["open"], rich_help_panel="Project setup"),
    ] = None
    no_interaction: Annotated[
        bool,
        typer.Option(
            "--no-interaction",
            help=CLI_HELP["no_interaction"],
            rich_help_panel="Terminal",
        ),
    ] = False
    no_color: Annotated[
        bool, typer.Option("--no-color", help=CLI_HELP["no_color"], rich_help_panel="Terminal")
    ] = False
    verbose: Annotated[
        bool, typer.Option("--verbose", help=CLI_HELP["verbose"], rich_help_panel="Terminal")
    ] = False

    def __post_init__(self) -> None:
        """
        Execute the parsed new-application command.

        Returns
        -------
        None
            Report the installation outcome through ``typer.Exit``.

        Raises
        ------
        typer.Exit
            On completion, cancellation, or a handled command failure.
        """
        _run_new(self)


new = app.command("new", help=CLI_HELP["new"])(_NewCommand)


def _run_new(command: _NewCommand) -> None:
    """
    Create an application and handle command diagnostics and exit codes.

    Parameters
    ----------
    command : _NewCommand
        Parsed application options, terminal preferences, and post-install consent.

    Returns
    -------
    None
        Report the installation outcome through ``typer.Exit``.

    Raises
    ------
    typer.Exit
        On completion, cancellation, or a handled command failure.
    """
    settings = command.context.obj or {}
    ui = UI(no_color=command.no_color or settings.get("no_color", False))
    try:
        if not command.no_interaction:
            ui.require_tty()
        runner = Runner()
        prerequisites = check_prerequisites(runner, cwd=Path.cwd(), announce=ui.message)
        ui.banner()
        if not command.no_interaction:
            _prompt_application(command, ui)
            _prompt_services(command, ui)
        plan = _build_plan(command)
        validate_destination(plan.path)
        if command.verbose or settings.get("verbose", False):
            ui.message(MESSAGES["verbose_plan"])
        ui.summary(plan)
        if not command.no_interaction and not ui.confirm(MESSAGES["confirm"], default=True):
            ui.message(MESSAGES["not_confirmed"])
            raise typer.Exit(0)
        ui.message(MESSAGES["installing"])
        with ui.progress() as progress:
            result = Installer(plan, prerequisites, runner, on_step=progress.step).install()
        options = PostInstallOptions(
            git=command.git, migrate=command.migrate, open=command.open_editor
        )
        if not command.no_interaction:
            ui.section(MESSAGES["section_setup"], MESSAGES["section_setup_hint"])
        run_post_install(
            result, options, prerequisites, runner, ui, no_interaction=command.no_interaction
        )
        ui.final(result)
        raise typer.Exit(result.exit_code)
    except Cancelled as exc:
        ui.warning(str(exc))
        raise typer.Exit(130) from None
    except KeyboardInterrupt, EOFError:
        ui.warning(MESSAGES["cancelled"])
        raise typer.Exit(130) from None
    except InstallerError as exc:
        ui.error(str(exc))
        raise typer.Exit(exc.exit_code) from None
    except (OSError, ValueError) as exc:
        # Avoid echoing unknown exception strings, which may contain subprocess secrets.
        ui.error(MESSAGES["unexpected"])
        raise typer.Exit(1) from exc


def _prompt_application(command: _NewCommand, ui: UI) -> None:
    """
    Prompt for application metadata omitted from the command.

    Parameters
    ----------
    command : _NewCommand
        Parsed options whose missing metadata is filled in place.
    ui : UI
        Interactive interface used to collect and validate user input.

    Returns
    -------
    None
        Store accepted metadata while preserving explicit values.
    """
    ui.section(MESSAGES["section_application"], MESSAGES["section_application_hint"])
    if command.stack is None:
        command.stack = Stack(
            ui.select(
                MESSAGES["stack"],
                STACK_CHOICES,
                DEFAULT_STACK.value,
                descriptions={key.value: source.description for key, source in STACKS.items()},
            )
        )
    if command.name is None:
        command.name = ui.text(MESSAGES["name"], "orionis-app", validate_name)
    if command.description is None:
        command.description = ui.text(
            MESSAGES["description"],
            DEFAULT_DESCRIPTION,
            _validate_description,
        )
    if command.author_name is None:
        command.author_name = ui.text(
            MESSAGES["author_name"],
            validator=_validate_author_name,
        )
    if command.author_email is None:
        command.author_email = ui.text(
            MESSAGES["author_email"],
            validator=_validate_optional_email,
        )


def _prompt_services(command: _NewCommand, ui: UI) -> None:
    """
    Prompt for omitted storage, database, and active driver selections.

    Parameters
    ----------
    command : _NewCommand
        Parsed options whose missing service selections are filled in place.
    ui : UI
        Interactive interface used to select supported drivers.

    Returns
    -------
    None
        Store accepted drivers while preserving explicit selections.
    """
    ui.section(MESSAGES["section_services"], MESSAGES["section_services_hint"])
    if command.storage is None:
        command.storage = Storage(
            ui.select(MESSAGES["storage"], STORAGE_CHOICES, Storage.LOCAL.value)
        )
    if command.storage == Storage.ALL and command.default_storage is None:
        command.default_storage = Storage(
            ui.select(MESSAGES["default_storage"], STORAGE_CHOICES[:-1], Storage.LOCAL.value)
        )
    if command.database is None:
        command.database = Database(
            ui.select(MESSAGES["database"], DATABASE_CHOICES, Database.SQLITE.value)
        )
    if command.database == Database.ALL and command.default_database is None:
        command.default_database = Database(
            ui.select(MESSAGES["default_database"], DATABASE_CHOICES[:-1], Database.SQLITE.value)
        )


def _build_plan(command: _NewCommand) -> InstallationPlan:
    """
    Build a validated installation plan using defaults for omitted values.

    Parameters
    ----------
    command : _NewCommand
        Explicit options and any metadata or drivers collected interactively.

    Returns
    -------
    InstallationPlan
        Validated metadata, destination, source, and driver selections.

    Raises
    ------
    ValidationError
        If metadata, the destination, or driver selections are invalid.
    """
    name = "orionis-app" if command.name is None else command.name
    return InstallationPlan(
        name=name,
        path=command.path if command.path is not None else Path.cwd() / name,
        description=DEFAULT_DESCRIPTION if command.description is None else command.description,
        author_name=command.author_name or None,
        author_email=command.author_email or None,
        stack=command.stack or DEFAULT_STACK,
        storage=command.storage or Storage.LOCAL,
        database=command.database or Database.SQLITE,
        default_storage=command.default_storage,
        default_database=command.default_database,
    )


def main() -> None:
    """
    Run the public Typer command-line entry point.

    Returns
    -------
    None
        Invoke the Typer application.

    Raises
    ------
    SystemExit
        When command dispatch terminates with its exit status.
    """
    app()


def _validate_description(value: str) -> str:
    """
    Validate a project description without allowing terminal controls.

    Parameters
    ----------
    value : str
        Project description entered by the user.

    Returns
    -------
    str
        Unchanged description after successful validation.

    Raises
    ------
    ValidationError
        If ``value`` contains terminal control characters.
    """
    return validate_text(value, MESSAGES["description"])


def _validate_author_name(value: str) -> str:
    """
    Validate an optional author name without allowing terminal controls.

    Parameters
    ----------
    value : str
        Author name entered by the user; an empty value is accepted.

    Returns
    -------
    str
        Unchanged author name after successful validation.

    Raises
    ------
    ValidationError
        If ``value`` contains terminal control characters.
    """
    return validate_text(value, MESSAGES["author_name"])


def _validate_optional_email(value: str) -> str:
    """
    Validate a nonempty author email and accept an empty value.

    Parameters
    ----------
    value : str
        Author email entered by the user; an empty value is accepted.

    Returns
    -------
    str
        Unchanged valid email address, or the original empty value.

    Raises
    ------
    ValidationError
        If a nonempty value has an invalid email format or contains controls.
    """
    return validate_email(value) if value else value
