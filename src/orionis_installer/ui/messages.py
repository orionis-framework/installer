"""Define English interface copy independently of terminal rendering."""

MESSAGES = {
    "tagline": "A new point in your constellation.",
    "name": "Application name",
    "description": "Description",
    "author_name": "Author name (optional)",
    "author_email": "Author email (optional)",
    "storage": "Which file storage service will the application use?",
    "default_storage": "Which disk should be the default?",
    "database": "Which database will the application use?",
    "default_database": "Which connection should be the default?",
    "confirm": "Create the application with this configuration?",
    "git": "Initialize Git in the project?",
    "migrate": "Run migrations and initial seeders?",
    "open": "Open the project in Visual Studio Code?",
    "no_tty": (
        "This terminal does not support interaction. Use 'orionis new --no-interaction' "
        "with the required options."
    ),
    "cancelled": "Installation cancelled by the user.",
    "not_confirmed": "Creation skipped; the project has not been downloaded.",
    "installing": "Downloading, configuring, and installing the application...",
    "summary": "Installation plan",
    "final": "Installation result",
    "ready": "Application ready.",
    "ready_warnings": "Application created; review warnings and pending states.",
    "not_ready": "The application is not ready; review the preceding diagnostic.",
    "navigation": "Arrows: select  |  Enter: accept  |  Ctrl+C: cancel",
    "yes": "Yes",
    "no": "No",
    "optional": "skipped",
    "next": "Next steps",
    "migration_pending": (
        "Migrations pending. Review the connection and configure secure seeders "
        "without example administrator credentials first."
    ),
    "unexpected": (
        "Unexpected installation error. A published project is preserved; "
        "review its state and use 'uv sync --python 3.14' to recover an incomplete installation."
    ),
    "verbose_plan": (
        "Diagnostic: the project environment is created at the final destination; "
        "target Python is 3.14.x."
    ),
    "warning_prefix": "Warning: ",
    "error_prefix": "Error: ",
    "default_marker": " (default)",
    "invalid_selector": "The selector requires choices and a valid default value.",
}

CLI_HELP = {
    "app": "Create Orionis applications with Python 3.14 and independent environments.",
    "new": "Create an application from the official Orionis skeleton.",
    "version": "Show the installer version.",
    "no_color": "Disable color; also honor NO_COLOR.",
    "verbose": "Enable additional diagnostics without exposing secrets.",
    "name": "Application name and default folder; skip its prompt when provided.",
    "path": "Final project path, rather than its parent directory.",
    "description": "Application description.",
    "author_name": "Author name; use an empty value to omit it.",
    "author_email": "Author email; use an empty value to omit it.",
    "storage": "File storage drivers.",
    "database": "Database drivers, including Redshift.",
    "default_storage": "Active disk with --storage all; defaults to local.",
    "default_database": "Active connection with --database all; defaults to sqlite.",
    "git": "Initialize Git after installation.",
    "migrate": "Run migrations and seeders with secure configuration.",
    "open": "Open the project in a new VS Code window.",
    "no_interaction": "Use safe defaults; Git, migrations, and editor require positive flags.",
}

STORAGE_CHOICES = [
    ("local", "Local"),
    ("s3", "Amazon S3"),
    ("azure", "Microsoft Azure Blob Storage"),
    ("gcs", "Google Cloud Storage"),
    ("all", "All cloud drivers"),
]

DATABASE_CHOICES = [
    ("sqlite", "SQLite"),
    ("mysql", "MySQL"),
    ("pgsql", "PostgreSQL"),
    ("oracle", "Oracle"),
    ("sqlserver", "SQL Server"),
    ("redshift", "Amazon Redshift"),
    ("all", "All available drivers"),
]

LABELS = {
    "name": "Application",
    "path": "Destination",
    "description": "Description",
    "author": "Author",
    "python": "Python",
    "framework": "Orionis",
    "storage": "Default disk",
    "database": "Default connection",
    "drivers_storage": "Storage drivers",
    "drivers_database": "Database drivers",
    "extras": "Orionis extras",
    "source": "Source",
    "creation": "Creation",
    "factories": "Factories",
    "git": "Git",
    "migrations": "Migrations and seeders",
    "editor": "Visual Studio Code",
}
