<h1 align="center">Orionis Framework</h1>

<p align="center">
  <img src="https://orionis-framework.com/favicon.svg" alt="Orionis Framework" width="180" />
</p>

<p align="center">
Create Orionis applications with a guided setup for your stack, database and storage.
</p>

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Git](https://git-scm.com/downloads)

uv manages Python **3.14.x** and the project's dependencies.

## Create a project

```bash
uvx --python 3.14 --from orionis-installer orionis new blog
```

Choose a stack, enter the project details, select your drivers and confirm the
installation. The defaults are **Blank**, local storage and SQLite.

The installer configures `.env`, generates `APP_KEY` and installs dependencies
in a project-local `.venv` with `uv.lock`. At the end, you can initialize Git
and run database migrations.

## Stacks

| Stack | Purpose |
| --- | --- |
| `master` | Full application with the framework's reference structure. |
| `blank` | Minimal foundation for a new project (default). |
| `ssr` | Server-side rendered applications. |
| `slim_api` | Lightweight HTTP APIs. |
| `slim_mcp` | Model Context Protocol applications. |
| `slim_worker` | Background jobs and scheduled tasks. |
| `slim_realtime` | WebSocket applications. |

## Customize the installation

Pass options directly to skip the corresponding questions:

```bash
uvx --python 3.14 --from orionis-installer orionis new api --stack slim_api --no-interaction
```

| Option | Purpose |
| --- | --- |
| `--stack NAME` | Select an application stack. |
| `--path PATH` | Set the destination directory; it must not already exist. |
| `--storage DRIVER` | Choose `local`, `s3`, `azure`, `gcs` or `all`. |
| `--database DRIVER` | Choose `sqlite`, `mysql`, `pgsql`, `oracle`, `sqlserver`, `redshift` or `all`. |
| `--git / --no-git` | Initialize Git or skip it. |
| `--migrate / --no-migrate` | Run database migrations or skip them. |
| `--no-interaction` | Skip all prompts and use defaults for unspecified options. |

With `all`, use `--default-storage` and `--default-database` to select the active
drivers. In non-interactive mode, Git and migrations run only when explicitly enabled.

Configure external database and cloud storage credentials in `.env`.
For all available options:

```bash
uvx --python 3.14 --from orionis-installer orionis new --help
```

## Run your project

Start an HTTP, SSR, MCP or realtime application:

```bash
cd blog
uv run python -B reactor serve
```

Apply pending application migrations:

```bash
uv run python -B reactor migrate
```

For Slim Worker, prepare the queue if migrations were skipped, then start the worker:

```bash
uv run python -B reactor queue:migrate
uv run python -B reactor queue:work
```
