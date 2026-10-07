# Orionis Installer

`orionis-installer` creates applications from the official Orionis skeleton on
`master`, with Python **3.14.x**, a project-local `.venv` and `uv.lock`. Its Python
module is `orionis_installer` and its executable is **`orionis`**.

The installer depends on CLI and configuration libraries. Orionis, database
drivers, cloud SDKs and Faker belong to the generated application's environment.
The English wizard supports arrow keys, Enter, visible defaults and immediate
validation. Its ASCII banner adapts to narrow terminals; `NO_COLOR` and
`--no-color` disable colors.

## Installation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and
[Git](https://git-scm.com/downloads). Python 3.14 or newer runs the installer; uv
selects stable Python 3.14.x for the application and can obtain it according to
its download and network policies.

When this distribution is available on your package index:

```bash
uvx --from orionis-installer orionis new
uvx --from orionis-installer orionis new blog
uvx --python 3.14 --from orionis-installer orionis new
uv tool install --python 3.14 orionis-installer
orionis new blog
orionis --help
orionis --version
```

`uvx --python 3.14` selects the **installer's** interpreter. The application has an
independent environment. The framework can also provide an `orionis` executable;
invoke the installer through `uvx --from orionis-installer orionis ...` and use
Reactor through the application's Python. Do not overwrite existing executables
with force flags.

For a local build:

```bash
uv build
uvx --python 3.14 --from ./dist/orionis_installer-0.1.0-py3-none-any.whl orionis --version
uvx --python 3.14 --from ./dist/orionis_installer-0.1.0-py3-none-any.whl orionis new blog
```

## Interactive wizard

After checking prerequisites, the wizard requests:

1. Application name, defaulting to `orionis-app`; `new blog` supplies it directly.
2. Description, defaulting to `A modern application built with Orionis Framework.`
3. Optional author name and email.
4. File storage drivers and, for `all`, the default disk.
5. Database drivers and, for `all`, the default connection.
6. Confirmation of the absolute destination, Python target, extras and source.

Explicit options skip their corresponding questions. After installation, the
wizard asks about Git initialization (Yes), migrations and initial seeders (No),
and Visual Studio Code (Yes), in that order. Ctrl+C cancels with exit code 130.
Without a TTY, use `--no-interaction`; help and version remain available.

Names use 1–100 ASCII letters, digits, dots, hyphens and underscores, with
alphanumeric endpoints. Path separators, traversal, controls and reserved
Windows names are rejected. Author names and descriptions support Unicode;
`--path` supports spaces and Unicode. Invalid names are diagnosed without silent
normalization. An existing destination is always rejected, even when empty.

## Non-interactive use

```bash
orionis new blog --no-interaction
orionis new blog --no-interaction --git --no-migrate --no-open
orionis new analytics --no-interaction --storage s3 --database redshift
orionis new team --no-interaction --storage all --database all
orionis new blog --no-interaction --path "./Project with spaces" --author-name "Jane Doe" --author-email jane@example.com
```

Defaults are `orionis-app`, the description above, no author, local storage and
SQLite. Aggregate driver selections default to local storage and SQLite unless
specified otherwise. Git, migrations and the editor are skipped unless their
positive flags are provided. This mode never requests credentials.

## CLI options

`--version`, `--no-color` and `--verbose` are global options. `--no-color` and
`--verbose` also work after `new`. Other options belong to `new`:

| Option | Behavior |
| --- | --- |
| `new [NAME]` | Application name and default folder name. |
| `--path PATH` | Final project location; its parent must exist. |
| `--description TEXT` | Application description. |
| `--author-name TEXT`, `--author-email TEXT` | Optional author metadata; empty values omit a field. |
| `--storage local\|s3\|azure\|gcs\|all` | File storage drivers to install. |
| `--default-storage local\|s3\|azure\|gcs` | Active disk with `all`; defaults to local. |
| `--database sqlite\|mysql\|pgsql\|oracle\|sqlserver\|redshift\|all` | Database drivers to install. |
| `--default-database sqlite\|mysql\|pgsql\|oracle\|sqlserver\|redshift` | Active connection with `all`; defaults to SQLite. |
| `--git / --no-git` | Initialize Git or skip it. |
| `--migrate / --no-migrate` | Request migrations and initial seeders or skip them. |
| `--open / --no-open` | Open Visual Studio Code or skip it. |
| `--no-interaction` | Apply safe defaults without reading input. |
| `--no-color` | Disable colors; also respects `NO_COLOR`, including an empty value. |
| `--verbose` | Show plan diagnostics without raw subprocess output or secrets. |

A default driver must be concrete. Without `all`, it can only repeat the selected
driver. There is no destructive `--force` option.

## Drivers and configuration

| Storage selection | Orionis extra |
| --- | --- |
| `local` | None |
| `s3` | `s3` |
| `azure` | `azure` |
| `gcs` | `gcs` |
| `all` | `storage` |

| Database selection | Orionis extra |
| --- | --- |
| `sqlite` | None |
| `mysql` | `mysql` |
| `pgsql` | `pgsql` |
| `oracle` | `oracle` |
| `sqlserver` | `sqlserver` |
| `redshift` | `redshift` |
| `all` | `database` |

Every application includes `factories`. Extras are sorted and deduplicated:
local + SQLite uses `factories`; S3 + Redshift uses `factories,redshift,s3`; both
aggregate selections use `database,factories,storage`. Aggregate extras install
drivers while migrations use one concrete connection. Installing an SDK creates
no buckets, accounts, permissions or infrastructure.

The installer preserves TOML comments, valid dependency groups, the application's
version and the skeleton's Orionis requirement. The application remains
unpackaged through `tool.uv.package = false`. `.env.example` is preferred;
`env.example` is supported. Only verified configuration keys are changed, and the
example remains intact. External connections and cloud storage require subsequent
credential configuration. See the [inspected contract](docs/sources.md).

APP_KEY is generated by the framework using the project interpreter, without
`--force`; existing keys are preserved. The official skeleton's administrative
seeder uses static example credentials, so automatic `--migrate` is blocked
before data changes. Review the [separate hardening patch](docs/seeder-hardening.patch)
and configure independent administrator credentials before manually running
Reactor. The installer does not automatically certify modified seeder code.

## Installation and recovery

The installer clones only the official `master` branch into a sibling staging
area, validates it, records its SHA and removes only that clone's `.git`. It
configures files before publication and then runs one main
`uv sync --python 3.14` at the final location. `.venv` is never moved from staging.
Python, the installed Orionis distribution, extras, lockfile, APP_KEY and effective
configuration are checked before reporting success.

Failures before publication clean only owned staging files. Failures after
publication preserve the project and report recovery steps. The operation is not
fully atomic across files, dependencies and database changes. An inherited uv
workspace or custom `UV_CONFIG_FILE` is rejected to protect isolation; use uv's
conventional configuration or documented environment variables for network,
index and download policies. See [security and isolation](docs/security.md).

| Exit code | Meaning |
| --- | --- |
| `0` | Application ready; requested operations completed or skipped, or confirmation declined. |
| `1` | Critical download, configuration, publication or installation failure. |
| `2` | Invalid arguments, missing prerequisites or an incompatible contract. |
| `3` | Project created, but a requested post-install operation failed. |
| `130` | User cancellation or end of input. |

Git runs only `git init`. It creates no commit, remote or push. Git and editor
failures preserve the project; independent post-install operations still run
unless cancelled. A failed migration may have changed data: inspect the database
before retrying. No automatic rollback or `migrate:fresh` is performed.

For a preserved project, review its configuration before recovering:

```bash
cd blog
uv sync --python 3.14
uv run python -B reactor key:generate
```

Start a ready application:

```bash
cd blog
uv run python -B reactor serve
```

After reviewing the connection and securing the seeders:

```bash
uv run python -B reactor migrate --seed
```

## Development

```bash
uv sync --locked --python 3.14
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv run --no-sync pytest --cov=orionis_installer --cov-report=term-missing
uv build
uv run --no-sync python scripts/verify_distribution.py
```

Normal tests use controlled terminal input, disposable processes and explicit
fixtures. Offline integration uses a local Git repository with simulated uv and
framework responses; it does not require Orionis or a network connection. The
distribution verification script installs the wheel into a clean temporary
environment and creates a disposable application through real uvx.

Enable the real smoke test explicitly. It uses the official skeleton, uv and a
disposable SQLite database, without running the example administrator seeder:

```bash
ORIONIS_REAL_SMOKE=1 uv run --no-sync pytest -m smoke -v
```

```powershell
$env:ORIONIS_REAL_SMOKE = '1'
uv run --no-sync pytest -m smoke -v
Remove-Item Env:ORIONIS_REAL_SMOKE
```

[CI](.github/workflows/ci.yml) checks Python 3.14 on Ubuntu, Windows and macOS,
including lint, formatting, types, tests, build and the clean-wheel entry point.
Runtime checks on an individual machine do not verify the other platforms or
external cloud/database services. CI does not publish the package.

Follow the English, typed documentation style of
[Orionis commands](https://github.com/orionis-framework/framework/blob/1.x/orionis/console/commands/support/key_generate.py)
and [configuration entities](https://github.com/orionis-framework/framework/blob/1.x/orionis/foundation/config/database/entities/connections.py).
Use concise triple-double-quoted NumPy docstrings for every function and method,
including private helpers and callbacks. Start with an imperative sentence;
include relevant Parameters, Returns, Yields and Raises sections, without an
Examples section. Ruff enforces the NumPy convention and D401; an AST-based test
checks documentation coverage for nested and private functions as well.

Source lives in `src/orionis_installer`; unit and integration tests live in
`tests`; technical contracts and policies live in `docs`; distribution verification
lives in `scripts`. Message catalogs remain centralized in `messages.py` and
`ui/messages.py`. Dependency constraints are declared in [pyproject.toml](pyproject.toml)
and development resolutions in [uv.lock](uv.lock). License: [MIT](LICENCE).

## Releasing to PyPI

Update the version in `pyproject.toml` and `src/orionis_installer/__init__.py`
together before a release. Configure PyPI authentication through
`UV_PUBLISH_TOKEN` or another [supported uv authentication method](https://docs.astral.sh/uv/guides/package/#publishing-your-package).
Run the root release script from PowerShell:

```powershell
.\release.ps1
```

The script requires `main` and an `origin` remote. It reads the manifest version,
cleans old builds, synchronizes Python 3.14, builds wheel/sdist, publishes to PyPI,
stages all changes, commits the release and pushes `main`. Versions with a major
component of at least 1 also create and push `v<version>` after checking for tag
conflicts. Successful releases clean generated builds. Failures stop the remaining
steps and retain artifacts for inspection or retry; completed uploads and Git
operations are not rolled back.
