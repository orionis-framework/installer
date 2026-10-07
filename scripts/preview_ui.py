"""Export the real terminal renderer with illustrative application data."""

import os
from io import StringIO
from pathlib import Path

from orionis_installer.models import InstallationPlan, InstallationResult, Stack, State
from orionis_installer.ui.output import Output


def main() -> None:
    """Export a reproducible SVG preview without creating an application.

    Returns
    -------
    None
        Write the documented preview using the installer's actual output components.
    """
    destination = Path(__file__).resolve().parents[1] / "docs" / "installer-preview.svg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    output = Output(file=StringIO(), width=104)
    output.console.record = True
    output.console.no_color = False
    output.console.legacy_windows = False
    plan = InstallationPlan(
        "atlas",
        Path("C:/Projects/atlas" if os.name == "nt" else "/projects/atlas"),
        stack=Stack.SSR,
        description="Your next remarkable Orionis application.",
    )
    output.banner()
    output.summary(plan)
    output.final(
        InstallationResult(
            plan,
            creation=State.COMPLETED,
            git=State.COMPLETED,
            migrations=State.COMPLETED,
            editor=State.SKIPPED,
            python_version="3.14.6",
            framework_version="0.801.0",
            published=True,
        )
    )
    output.console.save_svg(str(destination), title="Orionis / Installer")
    print(f"UI preview: {destination}")


if __name__ == "__main__":
    main()
