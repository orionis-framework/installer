# Orionis Installer

`orionis-installer` creates applications from a selectable Orionis stack, with
Python **3.14.x**, a project-local `.venv` and `uv.lock`. Its Python
module is `orionis_installer` and its executable is **`orionis`**.

Choose a stack, name your application, select storage and database drivers, then
review the installation plan. The installer downloads the skeleton, configures
the project and installs its dependencies in an independent environment.
After installation, you can initialize Git and prepare the application's database.
The final screen shows commands for the selected stack and leaves the choice of
editor to you. `NO_COLOR` and `--no-color` disable terminal colors.

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

## Interactive wizard

After checking prerequisites, the wizard requests:

1. Application stack: **Master**, **Blank** (the default), **SSR**, **Slim API**,
   **Slim MCP**, **Slim Worker** or **Slim Realtime**. The selector describes
   the purpose of each variant; the installation plan shows its source branch.
2. Application name, defaulting to `orionis-app`; `new blog` supplies it directly.
3. Description, defaulting to `A modern application built with Orionis Framework.`
4. Optional author name and email.
5. File storage drivers and, for `all`, the default disk.
6. Database drivers and, for `all`, the default connection.
7. Confirmation of the absolute destination, Python target, extras, stack and source branch.

Explicit options skip their corresponding questions. After installation, the
wizard asks about Git initialization (Yes) and database preparation (No),
in that order. Ctrl+C cancels with exit code 130.
Without a TTY, use `--no-interaction`; help and version remain available.

The console adapts to narrow terminals and ASCII output. Installation stages
show their status and elapsed time; redirected output remains readable without
animation. The opening screen uses local version information and requires no
network lookup. The framework version is reported after it has been installed
and verified.

Names use 1–100 ASCII letters, digits, dots, hyphens and underscores, with
alphanumeric endpoints. Path separators, traversal, controls and reserved
Windows names are rejected. Author names and descriptions support Unicode;
`--path` supports spaces and Unicode. Invalid names are diagnosed without silent
normalization. An existing destination is always rejected, even when empty.

## Non-interactive use

```bash
orionis new blog --no-interaction
orionis new blog --stack Blank --no-interaction --migrate
orionis new portal --stack SSR --no-interaction --migrate
orionis new service --stack slim_api --no-interaction
orionis new tools --stack slim_mcp --no-interaction
orionis new full-app --stack master --no-interaction --migrate
orionis new jobs --stack slim_worker --no-interaction --migrate
orionis new live --stack slim_realtime --no-interaction
orionis new blog --no-interaction --git --no-migrate
orionis new analytics --no-interaction --storage s3 --database redshift
orionis new team --no-interaction --storage all --database all
orionis new blog --no-interaction --path "./Project with spaces" --author-name "Jane Doe" --author-email jane@example.com
```

Defaults are the Blank stack, `orionis-app`, the description above, no author, local storage and
SQLite. Aggregate driver selections default to local storage and SQLite unless
specified otherwise. Git and migrations are skipped unless their
positive flags are provided. This mode never requests credentials.

## CLI options

`--version`, `--no-color` and `--verbose` are global options. `--no-color` and
`--verbose` also work after `new`. Other options belong to `new`:

| Option | Behavior |
| --- | --- |
| `new [NAME]` | Application name and default folder name. |
| `--stack master\|blank\|ssr\|slim_api\|slim_mcp\|slim_worker\|slim_realtime` | Select the catalog repository and branch; names are case-insensitive. Defaults to Blank. |
| `--path PATH` | Final project location; its parent must exist. |
| `--description TEXT` | Application description. |
| `--author-name TEXT`, `--author-email TEXT` | Optional author metadata; empty values omit a field. |
| `--storage local\|s3\|azure\|gcs\|all` | File storage drivers to install. |
| `--default-storage local\|s3\|azure\|gcs` | Active disk with `all`; defaults to local. |
| `--database sqlite\|mysql\|pgsql\|oracle\|sqlserver\|redshift\|all` | Database drivers to install. |
| `--default-database sqlite\|mysql\|pgsql\|oracle\|sqlserver\|redshift` | Active connection with `all`; defaults to SQLite. |
| `--git / --no-git` | Initialize Git or skip it. |
| `--migrate / --no-migrate` | Run application migrations and prepare required framework tables, or skip them. Seeders are never run automatically. |
| `--no-interaction` | Apply safe defaults without reading input. |
| `--no-color` | Disable colors; also respects `NO_COLOR`, including an empty value. |
| `--verbose` | Show plan diagnostics without raw subprocess output or secrets. |

A default driver must be concrete. Without `all`, it can only repeat the selected
driver. There is no destructive `--force` option.

## Stacks

| Stack | Starting application | Skeleton branch |
| --- | --- | --- |
| `master` | Full application matching the framework's reference structure. | `master` |
| `blank` | Minimal foundation for a new application; the default. | `blank_1.x` |
| `ssr` | Starting point for server-side rendered applications. | `ssr_1.x` |
| `slim_api` | Lightweight HTTP API application. | `slim_api_1.x` |
| `slim_mcp` | Starting point for Model Context Protocol applications. | `slim_mcp_1.x` |
| `slim_worker` | Console commands, scheduled tasks and background queue workers. | `slim_worker_1.x` |
| `slim_realtime` | WebSocket application with an HTTP health endpoint. | `slim_realtime_1.x` |

All seven stacks use the official Orionis skeleton. Only the selected branch is
downloaded; an unavailable branch stops installation without switching stacks.

The downloaded bootstrap determines which route files are registered. The
installer preserves each branch's routing, resources and configuration instead
of adding unrelated web, database or worker files. Each branch's own Orionis
dependency constraint is preserved; the current catalog targets Orionis 1.5.0.

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

Faker (for factories) and SQLite support are core dependencies in the
[framework manifest](https://github.com/orionis-framework/framework/blob/1.x/pyproject.toml);
the framework does not publish a `factories` extra. Extras are sorted and deduplicated:
local + SQLite needs no extras; S3 + Redshift uses `redshift,s3`; both
aggregate selections use `database,storage`. Aggregate extras install
drivers while migrations use one concrete connection. Installing an SDK creates
no buckets, accounts, permissions or infrastructure.

The installer preserves TOML comments, valid dependency groups, the application's
version and the skeleton's Orionis requirement. The application remains
unpackaged through `tool.uv.package = false`. `.env.example` is preferred;
`env.example` is supported. The generated `.env` groups database keys together
and storage keys together, separating groups with a blank line. Existing values
and unrelated settings are preserved. Quoted values use double quotes by default;
single quotes are used only when they avoid escaping embedded double quotes.
Backslashes and unavoidable inner quotes are escaped without changing the value.
Unquoted typed values remain unchanged. `APP_NAME` is readable text, not Base64;
numeric, boolean-like or null-like names use `str:` to preserve their string type
in Orionis. User-supplied literal credentials retain Base64 encoding to prevent
dotenv interpolation. Missing keys are inserted in their group,
including later connection settings requested during database preparation.
After `.env` has been written successfully, both example filenames are removed;
`.env.reference` remains unchanged as the complete configuration reference.
External connections and cloud storage require subsequent
credential configuration. If a skeleton omits the parent directory for its
declared application-local SQLite path, the installer creates that directory.
The database file is created on first migration; existing files are never truncated.

Database and filesystem configuration overrides are optional. Stacks such as
Slim MCP inherit Orionis defaults when those files are absent. Explicit overrides
are validated, and effective drivers are verified using the project interpreter
in both cases.

APP_KEY is generated by the framework using the project interpreter, without
`--force`; newly generated keys follow the same quoting policy, and existing keys
are preserved. `--migrate` uses that same interpreter
after verifying the selected connection. It runs application migrations and then
the framework commands required by the stack and its active database-backed
services:

| Condition | Framework command |
| --- | --- |
| Master or SSR authentication | `auth:migrate` |
| `CACHE_STORE=database` | `cache:migrate` |
| `QUEUE_CONNECTION=database` (the Slim Worker default) | `queue:migrate` |
| `TASKS_STORE=database` | `schedule:migrate` |
| `SESSION_DRIVER=database` | `session:migrate` |

Slim Realtime skips database preparation when it has no application migrations
or database-backed services. SQLite
works immediately; external databases require complete connection settings.
Framework commands create their internal tables from Orionis definitions;
duplicated application migrations are not added. Schema migrations run
independently of seeders. To populate initial data, review
the application's seeders and credentials, then invoke `reactor migrate --seed`
manually from the generated project.

## Installation and recovery

The installer clones the catalog's selected repository and branch into a sibling staging
area, validates its checked-out branch and revision and removes only that clone's
`.git`. It configures files before publication and then runs one main
`uv sync --python 3.14` at the final location. `.venv` is never moved from staging.
Python, the installed Orionis distribution, extras, lockfile, APP_KEY and effective
configuration are checked before reporting success.

Environment replacement and publication retry transient Windows access, sharing
and lock violations up to five attempts with bounded backoff. Publication rechecks
destination ownership and path redirections before every attempt; persistent and
unrelated I/O errors still stop the installation.

Failures before publication clean only owned staging files. Failures after
publication preserve the project and report recovery steps. The operation is not
fully atomic across files, dependencies and database changes. An inherited uv
workspace or custom `UV_CONFIG_FILE` is rejected to protect isolation; use uv's
conventional configuration or documented environment variables for network,
index and download policies.

| Exit code | Meaning |
| --- | --- |
| `0` | Application ready; requested operations completed or skipped, or confirmation declined. |
| `1` | Critical download, configuration, publication or installation failure. |
| `2` | Invalid arguments, missing prerequisites or an incompatible contract. |
| `3` | Project created, but a requested post-install operation failed. |
| `130` | User cancellation or end of input. |

Git runs only `git init`. It creates no commit, remote or push. Git and migration
failures preserve the project; independent post-install operations still run
unless cancelled. A failed migration may have changed data: inspect the database
before retrying. No automatic rollback or `migrate:fresh` is performed.

For a preserved project, review its configuration before recovering:

```bash
cd blog
uv sync --python 3.14
uv run python -B reactor key:generate
```

Start an HTTP, SSR, MCP or realtime application:

```bash
cd blog
uv run python -B reactor serve
```

For Slim MCP, connect [Orionis Inspector](https://inspector.orionis-framework.com/)
to the MCP endpoint printed in the project's README. For Slim Worker, prepare
the queue if you skipped `--migrate`, then start processing jobs:

```bash
cd jobs
uv run python -B reactor queue:migrate
uv run python -B reactor queue:work
```

Apply pending schema migrations:

```bash
uv run python -B reactor migrate
```

After reviewing the connection and securing the seeders, populate initial data:

```bash
uv run python -B reactor migrate --seed
```

## Code quality

Run the same lint, formatting and type checks used by CI:

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
```

Apply formatting with `uv run --no-sync ruff format .`. CI excludes the downloaded
skeleton checkouts so these checks cover only the installer's own code.

## Validating skeletons

Run offline tests with `uv run --no-sync pytest -m "not smoke"`. The real-branch
matrix additionally requires `ORIONIS_SKELETON_AUDIT` to point to a directory
containing separate `master`, `blank`, `ssr`, `slim_api`, `slim_mcp`, `slim_worker`
and `slim_realtime` checkouts of the
catalog's exact branches. An unset variable skips the optional audit; a configured
but incomplete directory stops the run once and names all missing branches.

Set `ORIONIS_REAL_SMOKE=1` and run `uv run --no-sync pytest -m smoke` to exercise
real installations and SQLite migrations for every stack, both with default
drivers and with all drivers installed. These tests require uv, Git and network
access; they do not connect to external database or cloud services.

## Releasing

Keep `project.version` in [pyproject.toml](pyproject.toml) and `__version__` in
[the package](src/orionis_installer/__init__.py) identical. After updating both,
run `uv lock` to refresh [uv.lock](uv.lock), then run
[RELEASE.ps1](RELEASE.ps1) from the `main` branch.
The script rejects mismatched versions before testing, building or publishing.
Before testing, it downloads every stack using the repository and branch in
[the source catalog](src/orionis_installer/models.py). No separate stack list or
inferred branch names are used for release checkouts. CI also checks out all
seven branches for the real-branch matrix.
It also commits and pushes project changes before publishing to PyPI.
The local release script is excluded from Git; its checkout regression runs only
when that script and the selected PowerShell edition are available.
