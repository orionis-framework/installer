from orionis_cli.api import OrionisFrameworkApi
from rich.console import Console
from rich.panel import Panel

class OrionisManager:
    """
    Main controller class for Orionis Framework CLI operations.

    Handles framework initialization, project creation, and help display functionality.
    All internal properties are properly privatized using name mangling.

    Attributes:
        __api (OrionisFrameworkApi): Private API client for framework data
        __version (str): Current framework version (private)
        __description (str): Framework description (private)
        __url (str): Official repository URL (private)
        __console (Console): Rich console instance (private)
        __withConsole (int): Precalculated console width for formatting (private)
    """

    def __init__(self) -> None:
        """
        Initialize the Orionis manager with framework metadata and console setup.

        Initializes:
        - Private API client
        - Framework version, description, and URL
        - Rich console with calculated display width
        """
        self.__api = OrionisFrameworkApi()
        self.__version = self.__api.getVersion()
        self.__description = self.__api.getDescription()
        self.__url = self.__api.getUrl()

        self.__console = Console()
        self.__withConsole = (self.__console.width // 4) * 3

    def new(self, name: str) -> dict:
        """
        Create a new Orionis project.

        Args:
            name (str): Name of the project to create

        Returns:
            dict: Installation result with status and metadata

        Note:
            Dynamically imports OrionisInstaller to avoid circular dependencies
        """
        try:
            self.welcome()
            from orionis_cli.core.setup import OrionisInstaller
            OrionisInstaller(name).handle()
        except Exception as e:
            self.__console.print(f"[red]Error:[/red] {str(e)}")
            return {"status": "error", "message": str(e)}

    def welcome(self) -> None:
        """
        Display a welcome message with framework information in a formatted rich Panel.

        Shows:
        - Framework version
        - Description
        - Repository URL
        """
        help_content = (
            "[yellow]★ Star us on GitHub![/yellow] :star:\n\n"
            f"[bold green]Version:[/bold green] [white]{self.__version}[/white]\n"
            f"[bold magenta]Description:[/bold magenta] [white]{self.__description}[/white]\n"
            f"[bold blue]Repository:[/bold blue] [underline]{self.__url}[/underline]"
        )

        self.__console.print()
        self.__console.print(
            Panel(
                help_content,
                border_style="blue",
                title="[bold cyan]🚀 Orionis CLI Installer[/bold cyan]",
                title_align='left',
                width=self.__withConsole,
                padding=(1, 1)
            )
        )
        self.__console.print()

    def help(self) -> None:
        """
        Display the Orionis CLI help information in a formatted rich Panel.

        Shows:
        - Framework version
        - Description
        - Repository URL
        - Basic usage commands
        """
        help_content = (
            "[yellow]★ Star us on GitHub![/yellow] :star:\n\n"
            f"[bold green]Version:[/bold green] [white]{self.__version}[/white]\n"
            f"[bold magenta]Description:[/bold magenta] [white]{self.__description}[/white]\n"
            f"[bold blue]Repository:[/bold blue] [underline]{self.__url}[/underline]\n"
            "\n[dim]Get started by running:[/dim] [bold blue]orionis new <app-name>[/bold blue]\n"
            "[dim]For help, use:[/dim] [bold blue]orionis --help[/bold blue]"
        )

        self.__console.print()
        self.__console.print(
            Panel(
                help_content,
                border_style="blue",
                title="[bold cyan]🚀 Orionis CLI Installer[/bold cyan]",
                title_align='left',
                width=self.__withConsole,
                padding=(1, 1)
            )
        )
        self.__console.print()

    def version(self) -> str:
        """
        Get the current version of the Orionis framework.

        Returns:
            str: Current version
        """
        self.__console.print(f"[bold green]Version:[/bold green] [white]{self.__version}[/white]")