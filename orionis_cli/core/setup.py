import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unicodedata import normalize
from typing import List, Optional
from rich.console import Console

class OrionisInstaller:
    """
    Handles the complete installation and setup of a new Orionis Framework project.

    This class manages the entire project creation workflow including:
    - Folder name validation and sanitization
    - Project skeleton cloning
    - Virtual environment creation
    - Dependency installation
    - Environment configuration
    - Security key generation
    - Git repository cleanup
    """

    __DOCS = "https://orionis-framework.com/"

    def __init__(self, name: str = 'example-app') -> None:
        """
        Initialize the installer with project name.

        Args:
            name (str): Name for the new project (default: 'example-app')
        """
        self.__nameAppFolder = self.__sanitizeFolderName(name)
        self.__skeletonRepoUrl = "https://github.com/orionis-framework/skeleton.git"
        self.__console = Console()
        self.__projectPath = Path(os.getcwd()) / self.__nameAppFolder

    def __sanitizeFolderName(self, name: str) -> str:
        """
        Sanitize and validate the project folder name.

        Args:
            name (str): Proposed folder name

        Returns:
            str: Sanitized folder name

        Raises:
            ValueError: For invalid folder names
        """
        if not name or not name.strip():
            raise ValueError("Project name cannot be empty.")

        name = name.strip()
        name = normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
        name = name.lower().replace(" ", "_")
        name = re.sub(r'[\\/:*?"<>|]', '', name)
        name = re.sub(r'[_-]{2,}', '_', name).strip('_-')[:255]

        if not re.match(r'^[a-z0-9_-]+$', name):
            raise ValueError(
                "Project name can only contain: "
                "lowercase letters, numbers, underscores, and hyphens"
            )

        return name or 'example-app'

    def __checkFolder(self) -> None:
        """Verify the target directory doesn't already exist."""
        if self.__projectPath.exists():
            raise ValueError(f"Directory '{self.__nameAppFolder}' already exists.")

    def __cloneRepository(self) -> None:
        """Clone the skeleton repository."""
        self.__runCommand(
            ["git", "clone", self.__skeletonRepoUrl, self.__nameAppFolder],
            "Cloning project skeleton",
            '.'
        )

    def __createVirtualEnv(self) -> None:
        """Create a Python virtual environment."""
        venvPath = self.__projectPath / "venv"
        self.__runCommand(
            [sys.executable, "-m", "venv", str(venvPath)],
            "Creating virtual environment"
        )

    def __installDependencies(self) -> None:
        """Install project dependencies."""
        requirements = self.__projectPath / "requirements.txt"
        if not requirements.exists():
            raise FileNotFoundError(
                f"requirements.txt not found. See documentation: {self.__DOCS}"
            )

        pip = self.__getVenvPip()
        self.__runCommand(
            [pip, "install", "-r", str(requirements)],
            "Installing dependencies"
        )

    def __setupEnvironment(self) -> None:
        """Configure environment files."""
        envExample = self.__projectPath / ".env.example"
        envFile = self.__projectPath / ".env"

        if envExample.exists():
            shutil.copy(str(envExample), str(envFile))
            self.__console.print("[green]✓[/] Created .env file")
        else:
            self.__console.print("[yellow]⚠[/] No .env.example found")

    def __cleanGitRepository(self) -> None:
        """Remove git origin and initialize fresh."""
        gitDir = self.__projectPath / ".git"
        if gitDir.exists():
            self.__runCommand(
                ["git", "remote", "remove", "origin"],
                "Cleaning git repository",
                cwd=str(self.__projectPath)
            )

    def __getVenvPython(self) -> str:
        """Get path to virtual environment Python executable."""
        venvBin = "Scripts" if os.name == "nt" else "bin"
        return str(self.__projectPath / "venv" / venvBin / "python")

    def __getVenvPip(self) -> str:
        """Get path to virtual environment pip executable."""
        venvBin = "Scripts" if os.name == "nt" else "bin"
        return str(self.__projectPath / "venv" / venvBin / "pip")

    def __runCommand(self, cmd: List[str], description: str, cwd: Optional[str] = None) -> None:
        """
        Execute a shell command with error handling.

        Args:
            cmd: Command and arguments as list
            description: Human-readable description of the operation
            cwd: Working directory (optional)
        """
        try:
            with self.__console.status(f"[cyan]{description}..."):
                subprocess.run(
                    cmd,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=cwd or str(self.__projectPath)
                )
            self.__console.print(f"[green]✓[/] {description}")
        except subprocess.CalledProcessError as e:
            self.__console.print(f"[red]✗[/] Failed to {description.lower()}")
            raise RuntimeError(f"Command failed: {' '.join(cmd)}") from e

    def handle(self) -> None:
        """
        Execute the complete project installation workflow.

        Steps:
        1. Validate project name
        2. Clone skeleton repository
        3. Create virtual environment
        4. Install dependencies
        5. Configure environment
        6. Generate security keys
        7. Clean up repository

        Raises:
            RuntimeError: If any installation step fails
        """
        steps = [
            {"desc": "Validating project name", "func": self.__checkFolder},
            {"desc": "Cloning the Orionis Framework skeleton repository", "func": self.__cloneRepository},
            {"desc": "Setting up the Python virtual environment", "func": self.__createVirtualEnv},
            {"desc": "Installing required project dependencies", "func": self.__installDependencies},
            {"desc": "Configuring environment variables", "func": self.__setupEnvironment},
            {"desc": "Finalizing project setup and cleaning repository", "func": self.__cleanGitRepository},
        ]

        try:

            console = Console()
            with console.status("[bold green]Creating Orionis Project...") as status:
                while steps:
                    task = steps.pop(0)
                    task['func']()

            # Suggest virtual environment activation based on OS
            if os.name == "nt":
                activate_cmd = "\\venv\\Scripts\\activate"
            else:
                activate_cmd = "source /venv/bin/activate"

            # Display success message
            self.__console.print(
                "\n[bold green]🎉 Project created successfully![/]\n"
                f"[bold]Name:[/] [cyan]{self.__nameAppFolder}[/]\n"
                f"[bold]Location:[/] [blue][u]'{self.__projectPath.resolve()}'[/u][/blue]\n"
                "\n[bold]Next steps:[/]\n"
                f"  [bold]1.[/] [green]cd {self.__nameAppFolder}[/]\n"
                f"  [bold]2.[/] [green]{activate_cmd}[/] [dim](activate virtual environment)[/]\n"
                f"  [bold]3.[/] [green]python -B reactor serve[/] [dim](run your application)[/]\n"
                f"\n[dim]See the docs for more: {self.__DOCS}[/]\n"
            )
            exit(0)

        except Exception as e:

            # Handle any exceptions that occur during the installation process
            self.__console.print(f"\n[bold red]✗ Installation failed: {e}[/]\n")
            exit(1)