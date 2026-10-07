"""Define English interface copy independently of terminal rendering."""

from orionis_installer.models import STACKS

MESSAGES = {
    "tagline": "A new point in your constellation.",
    "stack": "Choose your application stack",
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
    "migrate": "Run database migrations?",
    "open": "Open the project in Visual Studio Code?",
    "no_tty": (
        "This terminal does not support interaction. Use 'orionis new --no-interaction' "
        "with the required options."
    ),
    "cancelled": "Installation cancelled by the user.",
    "not_confirmed": "Creation skipped; the project has not been downloaded.",
    "installing": "Downloading, configuring, and installing the application...",
    "summary": "Installation plan",
    "summary_hint": "Review before creation",
    "final": "Installation result",
    "error_title": "Installation stopped",
    "ready": "Application ready.",
    "ready_warnings": "Application created; review warnings and pending states.",
    "not_ready": "The application is not ready; review the preceding diagnostic.",
    "navigation": "Up/Down: select  |  Enter: accept  |  Ctrl+C: cancel",
    "yes": "Yes",
    "no": "No",
    "optional": "skipped",
    "next": "Next steps",
    "next_hint": "Start your application from its project directory.",
    "migration_pending": ("Migrations pending. Review the database connection, then run:"),
    "section_application": "01  Application",
    "section_application_hint": "Choose a starting point and make it yours.",
    "section_services": "02  Services",
    "section_services_hint": "Select your storage and database. Install only what you need.",
    "section_setup": "03  Project setup",
    "section_setup_hint": "Your application is created. Choose the finishing steps.",
    "group_source": "Stack & runtime",
    "group_services": "Services & drivers",
    "group_runtime": "Runtime",
    "group_operations": "Project setup",
    "progress_title": "Creating your application",
    "progress_completed": "All installation stages completed and verified.",
    "progress_interrupted": "Installation interrupted. Review the following diagnostic.",
    "stage_source": "Source",
    "stage_configuration": "Configuration",
    "stage_dependencies": "Dependencies",
    "stage_verification": "Verification",
    "text_hint": "Enter: continue  |  Ctrl+C: cancel",
    "password_hint": "Input hidden  |  Enter: continue  |  Ctrl+C: cancel",
    "confirm_hint": "Left/Right: choose  |  Y/N: choose  |  Enter: confirm",
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
    "invalid_selector_duplicate": "The selector requires unique machine values.",
}

CLI_HELP = {
    "app": "Create Orionis applications with Python 3.14 and independent environments.",
    "new": "Create an application from the official Orionis skeleton.",
    "version": "Show the installer version.",
    "no_color": "Disable color; also honor NO_COLOR.",
    "verbose": "Enable additional diagnostics without exposing secrets.",
    "name": "Application name and default folder; skip its prompt when provided.",
    "stack": "Application stack; each catalog entry selects its repository and branch.",
    "path": "Final project path, rather than its parent directory.",
    "description": "Application description.",
    "author_name": "Author name; use an empty value to omit it.",
    "author_email": "Author email; use an empty value to omit it.",
    "storage": "File storage drivers.",
    "database": "Database drivers, including Redshift.",
    "default_storage": "Active disk with --storage all; defaults to local.",
    "default_database": "Active connection with --database all; defaults to sqlite.",
    "git": "Initialize Git after installation.",
    "migrate": "Run database migrations without executing seeders.",
    "open": "Open the project in a new VS Code window.",
    "no_interaction": "Use safe defaults; Git, migrations, and editor require positive flags.",
}

STACK_CHOICES = [(stack.value, source.label) for stack, source in STACKS.items()]

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

STORAGE_DESCRIPTIONS = {
    "local": "Files stored on the application's local disk.",
    "s3": "Object storage with the Amazon S3 SDK.",
    "azure": "Object storage with the Azure Blob Storage SDK.",
    "gcs": "Object storage with the Google Cloud Storage SDK.",
    "all": "Install every cloud SDK; select the active disk next.",
}

DATABASE_DESCRIPTIONS = {
    "sqlite": "A lightweight local database. No database server required.",
    "mysql": "Relational storage with the MySQL driver.",
    "pgsql": "Relational storage with the PostgreSQL driver.",
    "oracle": "Connect to Oracle Database.",
    "sqlserver": "Connect to Microsoft SQL Server using ODBC.",
    "redshift": "Connect to an Amazon Redshift data warehouse.",
    "all": "Install every database driver; select the active connection next.",
}

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
    "stack": "Stack",
    "repository": "Repository",
    "branch": "Branch",
    "creation": "Creation",
    "factories": "Factories",
    "git": "Git",
    "migrations": "Migrations",
    "editor": "Visual Studio Code",
}
