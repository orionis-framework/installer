# Orionis Installer

`orionis-installer` creates applications from a selectable Orionis stack, with
Python **3.14.x**, a project-local `.venv` and `uv.lock`. Its Python
module is `orionis_installer` and its executable is **`orionis`**.

Choose a stack, name your application, select storage and database drivers, then
review the installation plan. The installer downloads the skeleton, configures
the project and installs its dependencies in an independent environment.
After installation, you can initialize Git, run migrations and open the project
in Visual Studio Code. `NO_COLOR` and `--no-color` disable terminal colors.

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

1. Application stack: **Blank** (`blank_1.x`, the default) or **SSR** (`ssr_1.x`).
2. Application name, defaulting to `orionis-app`; `new blog` supplies it directly.
3. Description, defaulting to `A modern application built with Orionis Framework.`
4. Optional author name and email.
5. File storage drivers and, for `all`, the default disk.
6. Database drivers and, for `all`, the default connection.
7. Confirmation of the absolute destination, Python target, extras, stack and source branch.

Explicit options skip their corresponding questions. After installation, the
wizard asks about Git initialization (Yes), database migrations (No),
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
orionis new blog --stack Blank --no-interaction --migrate
orionis new portal --stack SSR --no-interaction --migrate
orionis new blog --no-interaction --git --no-migrate --no-open
orionis new analytics --no-interaction --storage s3 --database redshift
orionis new team --no-interaction --storage all --database all
orionis new blog --no-interaction --path "./Project with spaces" --author-name "Jane Doe" --author-email jane@example.com
```

Defaults are the Blank stack, `orionis-app`, the description above, no author, local storage and
SQLite. Aggregate driver selections default to local storage and SQLite unless
specified otherwise. Git, migrations and the editor are skipped unless their
positive flags are provided. This mode never requests credentials.

## CLI options

`--version`, `--no-color` and `--verbose` are global options. `--no-color` and
`--verbose` also work after `new`. Other options belong to `new`:

| Option | Behavior |
| --- | --- |
| `new [NAME]` | Application name and default folder name. |
| `--stack blank\|ssr` | Select the catalog repository and branch; names are case-insensitive. Defaults to Blank. |
| `--path PATH` | Final project location; its parent must exist. |
| `--description TEXT` | Application description. |
| `--author-name TEXT`, `--author-email TEXT` | Optional author metadata; empty values omit a field. |
| `--storage local\|s3\|azure\|gcs\|all` | File storage drivers to install. |
| `--default-storage local\|s3\|azure\|gcs` | Active disk with `all`; defaults to local. |
| `--database sqlite\|mysql\|pgsql\|oracle\|sqlserver\|redshift\|all` | Database drivers to install. |
| `--default-database sqlite\|mysql\|pgsql\|oracle\|sqlserver\|redshift` | Active connection with `all`; defaults to SQLite. |
| `--git / --no-git` | Initialize Git or skip it. |
| `--migrate / --no-migrate` | Run pending schema migrations or skip them. |
| `--open / --no-open` | Open Visual Studio Code or skip it. |
| `--no-interaction` | Apply safe defaults without reading input. |
| `--no-color` | Disable colors; also respects `NO_COLOR`, including an empty value. |
| `--verbose` | Show plan diagnostics without raw subprocess output or secrets. |

A default driver must be concrete. Without `all`, it can only repeat the selected
driver. There is no destructive `--force` option.

## Stacks

| Stack | Starting application | Skeleton branch |
| --- | --- | --- |
| `blank` | Minimal foundation for a new application; the default. | `blank_1.x` |
| `ssr` | Starting point for server-side rendered applications. | `ssr_1.x` |

Both stacks use the official Orionis skeleton. Only the selected branch is
downloaded; an unavailable branch stops installation without switching stacks.

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
credential configuration.

APP_KEY is generated by the framework using the project interpreter, without
`--force`; existing keys are preserved. `--migrate` executes `reactor migrate`
through that same interpreter after verifying the selected connection. SQLite
works immediately; external databases require complete connection settings.
Schema migrations run independently of seeders. To populate initial data, review
the application's seeders and credentials, then invoke `reactor migrate --seed`
manually from the generated project.

## Installation and recovery

The installer clones the catalog's selected repository and branch into a sibling staging
area, validates its checked-out branch and revision and removes only that clone's
`.git`. It configures files before publication and then runs one main
`uv sync --python 3.14` at the final location. `.venv` is never moved from staging.
Python, the installed Orionis distribution, extras, lockfile, APP_KEY and effective
configuration are checked before reporting success.

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

Apply pending schema migrations:

```bash
uv run python -B reactor migrate
```

After reviewing the connection and securing the seeders, populate initial data:

```bash
uv run python -B reactor migrate --seed
```

