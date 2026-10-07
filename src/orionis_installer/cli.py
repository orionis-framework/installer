"""Build a validated installation plan through the public Typer command."""

from pathlib import Path
from typing import Annotated

import typer

from orionis_installer import __version__
from orionis_installer.exceptions import Cancelled, InstallerError
from orionis_installer.installer import Installer
from orionis_installer.models import (
    DEFAULT_DESCRIPTION,
    Database,
    InstallationPlan,
    PostInstallOptions,
    Storage,
)
from orionis_installer.post_install import run_post_install
from orionis_installer.prerequisites import check_prerequisites
from orionis_installer.processes import Runner
from orionis_installer.ui import UI
from orionis_installer.ui.messages import CLI_HELP, DATABASE_CHOICES, MESSAGES, STORAGE_CHOICES
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
    """Print the installer version when the eager flag is enabled.

    Parameters
    ----------
    value : bool
        Whether the caller requested version information.

    Raises
    ------
    typer.Exit
        If version information was requested, after printing it.
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
    """Store global terminal preferences in the command context.

    Parameters
    ----------
    context : typer.Context
        Context shared with the selected command.
    version : bool, optional
        Eager version flag handled by its callback before command execution.
    no_color : bool, optional
        Whether all terminal output should disable color.
    verbose : bool, optional
        Whether commands should include additional safe diagnostics.
    """
    context.ensure_object(dict)
    context.obj.update(no_color=no_color, verbose=verbose)


@app.command("new", help=CLI_HELP["new"])
def new(
    context: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(help=CLI_HELP["name"]),
    ] = None,
    path: Annotated[Path | None, typer.Option("--path", help=CLI_HELP["path"])] = None,
    description: Annotated[
        str | None, typer.Option("--description", help=CLI_HELP["description"])
    ] = None,
    author_name: Annotated[
        str | None, typer.Option("--author-name", help=CLI_HELP["author_name"])
    ] = None,
    author_email: Annotated[
        str | None, typer.Option("--author-email", help=CLI_HELP["author_email"])
    ] = None,
    storage: Annotated[Storage | None, typer.Option("--storage", help=CLI_HELP["storage"])] = None,
    database: Annotated[
        Database | None,
        typer.Option("--database", help=CLI_HELP["database"]),
    ] = None,
    default_storage: Annotated[
        Storage | None,
        typer.Option("--default-storage", help=CLI_HELP["default_storage"]),
    ] = None,
    default_database: Annotated[
        Database | None,
        typer.Option("--default-database", help=CLI_HELP["default_database"]),
    ] = None,
    git: Annotated[bool | None, typer.Option("--git/--no-git", help=CLI_HELP["git"])] = None,
    migrate: Annotated[
        bool | None,
        typer.Option("--migrate/--no-migrate", help=CLI_HELP["migrate"]),
    ] = None,
    open_editor: Annotated[
        bool | None,
        typer.Option("--open/--no-open", help=CLI_HELP["open"]),
    ] = None,
    no_interaction: Annotated[
        bool,
        typer.Option(
            "--no-interaction",
            help=CLI_HELP["no_interaction"],
        ),
    ] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help=CLI_HELP["no_color"])] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help=CLI_HELP["verbose"])] = False,
) -> None:
    """Create and verify an application from explicit options or wizard input.

    Parameters
    ----------
    context : typer.Context
        Context containing global terminal preferences.
    name : str or None, optional
        Application name; prompt interactively or use the default when omitted.
    path : Path or None, optional
        Final destination, defaulting to the named folder in the current directory.
    description : str or None, optional
        Project description; omission selects a prompt or the standard description.
    author_name : str or None, optional
        Optional author name; an explicit empty value omits the author field.
    author_email : str or None, optional
        Optional author email; an explicit empty value omits the email field.
    storage : Storage or None, optional
        Storage drivers to install, defaulting to local storage.
    database : Database or None, optional
        Database drivers to install, defaulting to SQLite.
    default_storage : Storage or None, optional
        Concrete active disk when all storage drivers are selected.
    default_database : Database or None, optional
        Concrete active connection when all database drivers are selected.
    git : bool or None, optional
        Explicit Git consent, or no decision until the post-install phase.
    migrate : bool or None, optional
        Explicit migration consent, or no decision until the post-install phase.
    open_editor : bool or None, optional
        Explicit editor consent, or no decision until the post-install phase.
    no_interaction : bool, optional
        Whether to avoid prompts and apply safe defaults.
    no_color : bool, optional
        Whether this command should disable terminal colors.
    verbose : bool, optional
        Whether to include additional diagnostics without exposing secrets.

    Raises
    ------
    typer.Exit
        On completion, validation failure, installation failure, or cancellation,
        with the corresponding stable command exit code.
    """
    settings = context.obj or {}
    ui = UI(no_color=no_color or settings.get("no_color", False))
    try:
        if not no_interaction:
            ui.require_tty()
        runner = Runner()
        prerequisites = check_prerequisites(runner, cwd=Path.cwd(), announce=ui.message)
        ui.banner()
        if not no_interaction:
            if name is None:
                name = ui.text(MESSAGES["name"], "orionis-app", validate_name)
            if description is None:
                description = ui.text(
                    MESSAGES["description"],
                    DEFAULT_DESCRIPTION,
                    _validate_description,
                )
            if author_name is None:
                author_name = ui.text(
                    MESSAGES["author_name"],
                    validator=_validate_author_name,
                )
            if author_email is None:
                author_email = ui.text(
                    MESSAGES["author_email"],
                    validator=_validate_optional_email,
                )
            if storage is None:
                storage = Storage(
                    ui.select(MESSAGES["storage"], STORAGE_CHOICES, Storage.LOCAL.value)
                )
            if storage == Storage.ALL and default_storage is None:
                default_storage = Storage(
                    ui.select(
                        MESSAGES["default_storage"], STORAGE_CHOICES[:-1], Storage.LOCAL.value
                    )
                )
            if database is None:
                database = Database(
                    ui.select(MESSAGES["database"], DATABASE_CHOICES, Database.SQLITE.value)
                )
            if database == Database.ALL and default_database is None:
                default_database = Database(
                    ui.select(
                        MESSAGES["default_database"], DATABASE_CHOICES[:-1], Database.SQLITE.value
                    )
                )
        name = "orionis-app" if name is None else name
        plan = InstallationPlan(
            name=name,
            path=path if path is not None else Path.cwd() / name,
            description=DEFAULT_DESCRIPTION if description is None else description,
            author_name=author_name or None,
            author_email=author_email or None,
            storage=storage or Storage.LOCAL,
            database=database or Database.SQLITE,
            default_storage=default_storage,
            default_database=default_database,
        )
        validate_destination(plan.path)
        if verbose or settings.get("verbose", False):
            ui.message(MESSAGES["verbose_plan"])
        ui.summary(plan)
        if not no_interaction and not ui.confirm(MESSAGES["confirm"], default=True):
            ui.message(MESSAGES["not_confirmed"])
            raise typer.Exit(0)
        ui.message(MESSAGES["installing"])
        result = Installer(plan, prerequisites, runner, on_step=ui.message).install()
        options = PostInstallOptions(git=git, migrate=migrate, open=open_editor)
        run_post_install(result, options, prerequisites, runner, ui, no_interaction=no_interaction)
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


def main() -> None:
    """Run the public command-line entry point.

    Raises
    ------
    SystemExit
        When Typer finishes processing the command with its exit status.
    """
    app()


def _validate_description(value: str) -> str:
    """Reject terminal controls in a project description.

    Parameters
    ----------
    value : str
        Description entered by the user.

    Returns
    -------
    str
        Original description after validation.

    Raises
    ------
    ValidationError
        If the description contains control characters.
    """
    return validate_text(value, MESSAGES["description"])


def _validate_author_name(value: str) -> str:
    """Reject terminal controls while allowing an omitted author name.

    Parameters
    ----------
    value : str
        Author name entered by the user, possibly empty.

    Returns
    -------
    str
        Original author name after validation.

    Raises
    ------
    ValidationError
        If the name contains control characters.
    """
    return validate_text(value, MESSAGES["author_name"])


def _validate_optional_email(value: str) -> str:
    """Validate an author email while accepting an omitted value.

    Parameters
    ----------
    value : str
        Email address entered by the user, possibly empty.

    Returns
    -------
    str
        Original valid address or the empty value.

    Raises
    ------
    ValidationError
        If a nonempty email has an invalid format or contains controls.
    """
    return validate_email(value) if value else value
