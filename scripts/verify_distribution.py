"""Verify the built wheel outside the source tree and through real uvx.

This deliberately uses network and creates only disposable applications. It never
runs the skeleton's privileged example seeder or connects to external databases.
"""

import json
import os
import tempfile
from pathlib import Path

from orionis_installer import __version__
from orionis_installer.configuration import read_env
from orionis_installer.installer import project_python
from orionis_installer.prerequisites import check_prerequisites
from orionis_installer.processes import Runner, resolve_executable


def main() -> None:
    """
    Verify wheel isolation and create a disposable application through uvx.

    Returns
    -------
    None
        Check the installed entry point, application runtime and seeder guard.

    Raises
    ------
    AssertionError
        If distribution contents or application behavior violate the contract.
    """
    wheel = (
        Path(__file__).resolve().parents[1]
        / "dist"
        / (f"orionis_installer-{__version__}-py3-none-any.whl")
    )
    assert wheel.is_file(), "Build the distribution first with uv build."
    runner = Runner()
    prerequisites = check_prerequisites(runner, cwd=Path.cwd())
    uvx = resolve_executable("uvx", Path.cwd())
    assert uvx is not None, "A real uvx launcher is required for this verification."
    with tempfile.TemporaryDirectory(prefix="orionis-wheel-verification-") as temporary:
        root = Path(temporary)
        environment = root / "clean wheel environment"
        runner.run([prerequisites.uv, "venv", "--python", "3.14", environment], cwd=root)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        entry = scripts / ("orionis.exe" if os.name == "nt" else "orionis")
        runner.run([prerequisites.uv, "pip", "install", "--python", python, wheel], cwd=root)
        for arguments in (["--version"], ["--help"], ["new", "--help"]):
            response = runner.run([entry, *arguments], cwd=root)
            assert response.stdout.strip()
        response = runner.run(
            [
                python,
                "-I",
                "-c",
                "import json,importlib.metadata as m,orionis_installer as i; "
                "print(json.dumps({'file':i.__file__,'packages':[d.metadata['Name'] "
                "for d in m.distributions()]}))",
            ],
            cwd=root,
        )
        data = json.loads(response.stdout)
        assert Path(data["file"]).is_relative_to(environment)
        forbidden = {"orionis", "faker", "boto3", "oracledb", "redshift-connector"}
        assert not forbidden & {name.lower() for name in data["packages"]}
        print(
            "Clean wheel: entry point, help, version and installer-only dependencies verified.",
            flush=True,
        )
        version = runner.run(
            [uvx, "--python", "3.14", "--from", wheel, "orionis", "--version"], cwd=root
        )
        assert f"orionis-installer {__version__}" in version.stdout
        project = root / "application with spaces ñ &"
        response = runner.run(
            [
                uvx,
                "--python",
                "3.14",
                "--from",
                wheel,
                "orionis",
                "new",
                "wheel-smoke",
                "--path",
                project,
                "--no-interaction",
                "--no-color",
                "--git",
                "--migrate",
                "--no-open",
            ],
            cwd=root,
            timeout=900,
            check=False,
        )
        assert response.returncode == 3, (
            "Requested unsafe example seeding must fail only post-install."
        )
        assert "administrator seeder" in response.stdout
        assert (project / ".git").is_dir() and (project / "uv.lock").is_file()
        assert read_env(project / ".env")["APP_KEY"]
        assert not (project / "database" / "database.sqlite").exists()
        child = runner.run(
            [
                project_python(project),
                "-B",
                "-c",
                "import json,sys,importlib.metadata as m; "
                "from faker import Faker; assert Faker().name(); "
                "print(json.dumps({'python':sys.version.split()[0],'orionis':m.version('orionis')}))",
            ],
            cwd=project,
        )
        runtime = json.loads(child.stdout)
        ignored = runner.run(
            [prerequisites.git, "check-ignore", "--no-index", ".env", ".venv/", "uv.lock"],
            cwd=project,
        ).stdout.splitlines()
        assert ".env" in ignored and ".venv/" in ignored and "uv.lock" not in ignored
        print(
            f"Real uvx wheel: Python {runtime['python']}, Orionis {runtime['orionis']}; "
            "factories, final venv, Git init, secret ignores and safe seeder refusal verified.",
            flush=True,
        )


if __name__ == "__main__":
    main()
