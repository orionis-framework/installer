MESSAGES: dict[str, str] = {
    "project_environment_missing": "uv did not create a local .venv directly inside the project.",
    "project_interpreter_missing": "The project environment has no Python interpreter.",
    "project_probe_invalid": "The project verification did not return valid metadata.",
    "project_python_invalid": "The actual project Python version is not 3.14.x.",
    "project_environment_redirected": (
        "Synchronization was redirected outside the project environment."
    ),
    "framework_requirement_mismatch": (
        "The installed distribution does not satisfy the skeleton requirement."
    ),
    "framework_extras_missing_prefix": (
        "The resolved distribution does not publish the required extras: "
    ),
    "aggregate_drivers_missing": "The {aggregate} aggregate does not publish every driver.",
    "aggregate_drivers_incomplete": (
        "The {aggregate} aggregate does not include every offered driver."
    ),
    "framework_dependency_missing": (
        "An effective Orionis dependency is missing or has an incompatible version."
    ),
    "effective_configuration_mismatch": (
        "The effective configuration does not match the selected plan."
    ),
    "app_key_missing": "Bootstrap did not generate APP_KEY; check framework compatibility.",
    "app_key_changed": "Bootstrap changed an existing APP_KEY; review the project.",
    "phase_clone": "Cloning {stack} from {repository}@{branch} into staging...",
    "phase_configuration": "Skeleton validated. Configuring the application and extras...",
    "phase_sync": (
        "Project published. Synchronizing dependencies with uv at the final destination..."
    ),
    "phase_verification": (
        "Synchronization finished. Verifying Python, Orionis, and effective configuration..."
    ),
    "project_lock_missing": "uv did not create the application lockfile.",
    "cloud_configuration_pending": (
        "Cloud drivers installed; configure credentials, bucket/container, and "
        "permissions. No infrastructure was created."
    ),
    "database_configuration_pending": (
        "Configure DB_HOST, {database_key}, DB_USERNAME, and DB_PASSWORD before "
        "migrating; 'configure-me' marks pending configuration."
    ),
    "oracle_connection_keys": "DB_SERVICE_NAME (or DB_SID/DB_DSN/DB_TNS)",
    "sqlserver_system_driver_required": (
        "SQL Server requires the system ODBC driver specified by DB_ODBC_DRIVER."
    ),
    "installation_cancelled": "Installation cancelled.",
    "installation_files_failed": (
        "A file operation failed; check permissions and available disk space."
    ),
    "installation_recovery": (
        " Project preserved at {path}. Review its configuration and run there: uv sync "
        "--python 3.14; then: uv run python -B reactor key:generate (without --force)."
    ),
    "requirement_invalid": "The skeleton contains an invalid dependency requirement.",
    "requirement_ambiguous": (
        "The skeleton must declare exactly one unambiguous Orionis requirement."
    ),
    "requirement_url_unsupported": (
        "Orionis must resolve as a distribution, rather than from an external URL."
    ),
    "skeleton_python_unsupported": (
        "The skeleton does not support the selected Python 3.14.x interpreter."
    ),
    "framework_marker_unsupported": "The Orionis marker excludes Python 3.14 on this platform.",
    "skeleton_dependency_redirect": (
        "The skeleton must not redirect dependencies to workspaces or sources."
    ),
    "manifest_incompatible": "pyproject.toml does not satisfy the application contract.",
    "environment_example_ambiguous": (
        ".env.example and env.example differ; the contract is ambiguous."
    ),
    "environment_example_missing": "The skeleton has neither .env.example nor env.example.",
    "skeleton_configuration_unreadable": "The skeleton configuration cannot be inspected.",
    "skeleton_selection_unsupported": "The skeleton cannot represent the selection '{active}'.",
    "environment_base64_invalid": "A base64 value in .env is invalid.",
    "environment_keys_missing": (
        "The environment example does not declare every required verified key."
    ),
    "sqlite_path_invalid": "SQLite requires a relative local path consistent with the skeleton.",
    "sqlite_directory_missing": "The SQLite directory declared by the skeleton does not exist.",
    "oracle_service_unsupported": "The skeleton cannot represent DB_SERVICE_NAME for Oracle.",
    "staging_redirect": "The staging directory contains a redirection.",
    "skeleton_redirect": "The skeleton contains links or junctions; clone rejected.",
    "skeleton_files_missing": "The skeleton lacks files required by the contract.",
    "skeleton_runtime_distributed": (
        "The skeleton must not distribute .venv or an operational .env."
    ),
    "cleanup_staging_redirect": "A staging directory replaced by a link will not be removed.",
    "cleanup_staging_replaced": (
        "The staging directory was replaced; the foreign directory is preserved."
    ),
    "cleanup_tree_redirect": "A directory tree replaced by links will not be cleaned.",
    "destination_reserved": (
        "Another installation reserved this destination; it will not be overwritten."
    ),
    "destination_race": "The destination appeared during installation; it is preserved intact.",
    "publication_destination_replaced": (
        "The destination was replaced; publication stopped before writing outside it."
    ),
    "publication_ancestor_redirect": "An ancestor path was replaced by a redirection.",
    "publication_staging_redirect": (
        "The staging directory contains a new redirection; publication stopped."
    ),
    "skeleton_revision_invalid": "Git did not return a valid provenance SHA.",
    "source_branch_invalid": (
        "The skeleton must check out the selected Git branch '{branch}'; "
        "a tag or a different branch cannot provide its provenance."
    ),
    "skeleton_source_context": "{diagnostic} (stack source: {repository}@{branch})",
    "state_pending": "pending",
    "state_running": "running",
    "state_completed": "completed",
    "state_skipped": "skipped",
    "state_failed": "failed",
    "state_cancelled": "cancelled",
    "plan_drivers_invalid": "The plan requires valid storage and database choices.",
    "plan_stack_invalid": "Choose a configured application stack: blank or ssr.",
    "skeleton_source_invalid": (
        "Stack sources require clean metadata, a credential-free HTTPS repository, "
        "and a valid explicit Git branch."
    ),
    "description_label": "Description",
    "author_label": "Author",
    "default_driver_invalid": "The default driver must be a concrete choice.",
    "default_storage_invalid": "--default-storage requires --storage all or the same driver.",
    "default_database_invalid": "--default-database requires --database all or the same engine.",
    "destination_label": "Destination",
    "text_control_invalid": "{label}: control characters are not allowed.",
    "name_invalid": (
        "Invalid name: use 1-100 ASCII alphanumeric characters, '.', '-', or '_', with "
        "alphanumeric ends, without '..' or reserved Windows names. For example, use "
        "'my-app' instead of 'my app'; names are not normalized automatically."
    ),
    "email_label": "Email",
    "email_invalid": "Invalid email: provide an address such as name@example.com.",
    "destination_ambiguous": "The destination must not contain traversal or ambiguous components.",
    "destination_exists": (
        "The destination already exists; choose a new path (--force is not available)."
    ),
    "destination_parent_missing": "The destination parent directory must exist.",
    "destination_redirect": "The destination traverses a link or junction; choose a direct path.",
    "ancestor_manifest_invalid": "The ancestor manifest could not be checked.",
    "ancestor_workspace_unsupported": "Applications cannot be created inside a uv workspace.",
    "seeders_directory_redirect": "The seeders directory contains a redirection.",
    "seeders_tree_redirect": "The seeders contain links or junctions.",
    "seeders_unreadable": "The seeders could not be reviewed; this operation remains pending.",
    "seeders_unsafe": (
        "The skeleton administrator seeder requires review: executable seeder code "
        "cannot be certified automatically. Review its credentials and administrator "
        "inputs before running migrate --seed."
    ),
    "connection_configuration_prompt": "Connection details are missing. Configure them now?",
    "connection_incomplete": "The connection is still incomplete; migration remains pending.",
    "connection_port_invalid": "Invalid port; migration remains pending.",
    "connection_display_hidden": "[value hidden; review .env]",
    "connection_changed": "The effective connection changed; review .env before migrating.",
    "non_interactive_connection_incomplete": (
        "--migrate requires a complete connection; configure .env and run Reactor again."
    ),
    "connection_invalid": "The connection is still invalid; migration remains pending.",
    "connection_display": "Connection: {connection}; database/service: {database}",
    "connection_display_host": "; host: {host}",
    "migration_data_warning": ". Applying pending schema migrations.",
    "post_git_prompt": "Initialize Git in the project?",
    "post_migration_prompt": "Apply database migrations?",
    "post_editor_prompt": "Open the project in Visual Studio Code?",
    "editor_missing": (
        "Visual Studio Code was not found. Open the project folder manually using File > "
        "Open Folder."
    ),
    "post_cancelled": "Operation cancelled. Project preserved at {path}.",
    "post_files_failed": "A post-install file operation failed.",
    "migration_partial_warning": (
        " Some migrations may have been applied. Review the project "
        "state and run uv run python -B reactor migrate again; no rollback or "
        "migrate:fresh was performed."
    ),
    "connection_host_label": "Host",
    "connection_port_label": "Port",
    "connection_username_label": "Username",
    "connection_password_label": "Password",
    "connection_oracle_service_label": "Oracle service",
    "connection_database_label": "Database name",
    "python_probe_failed": "The actual Python version could not be verified.",
    "python_stable_required": "The application requires a stable Python 3.14.x release.",
    "uv_missing": (
        "uv was not found on a trusted path. Install it using "
        "https://docs.astral.sh/uv/getting-started/installation/ and run orionis again."
    ),
    "git_missing": (
        "Git is required. Install it from https://git-scm.com/downloads and check `git "
        "--version` before running orionis again."
    ),
    "prerequisite_wrapper_unsupported": (
        "uv and Git require native executables rather than shell scripts."
    ),
    "prerequisite_execution_failed": "uv or Git could not be executed; check their installation.",
    "python_downloads_disabled": (
        "Python 3.14 was not found and downloads are disabled by "
        "UV_PYTHON_DOWNLOADS=never. Provide Python 3.14 locally."
    ),
    "python_offline_missing": (
        "Python 3.14 was not found and uv is offline. Provide Python 3.14 locally."
    ),
    "python_download_announcement": (
        "uv will attempt to obtain Python 3.14 while respecting its network and download "
        "configuration."
    ),
    "python_download_failed": (
        "uv could not obtain Python 3.14. Check the network, python-downloads policy, and"
        " uv version; then try again."
    ),
    "python_find_failed": "uv did not find Python 3.14 after attempting to obtain it.",
    "python_find_invalid": "uv returned an invalid Python path.",
    "python_working_directory_invalid": (
        "Python must not come from the installer working directory."
    ),
    "executable_name_invalid": "The executable name must be a simple name.",
    "editor_wrapper_unreadable": "The Visual Studio Code launcher could not be read.",
    "editor_wrapper_incompatible": "The Visual Studio Code launcher has an unsupported format.",
    "editor_wrapper_ambiguous": "The Visual Studio Code launcher contains ambiguous paths.",
    "editor_wrapper_outside": "The Visual Studio Code launcher points outside its installation.",
    "editor_components_missing": "The official Visual Studio Code components were not found.",
    "windows_process_isolation_failed": "The Windows process group could not be isolated.",
    "windows_threads_enumeration_failed": "The controlled process threads could not be enumerated.",
    "windows_primary_thread_missing": "The controlled process primary thread was not identified.",
    "windows_thread_open_failed": "The controlled process thread could not be opened.",
    "windows_thread_resume_failed": "The controlled process could not be resumed.",
    "process_arguments_invalid": "Provide a list of separate arguments.",
    "process_arguments_null": "Arguments must not contain null characters.",
    "process_executable_invalid": "The process requires a native executable with an absolute path.",
    "process_working_directory_invalid": "The working directory and timeout must be valid.",
    "process_start_failed": "The requested process could not be started; check prerequisites.",
    "process_group_failed": "The Windows process group could not be controlled.",
    "process_timeout": "The operation exceeded the {timeout:g}-second timeout.",
    "process_exit_failed": "The external operation failed (exit code {code}).",
    "process_cancelled": "Operation cancelled; controlled processes were stopped.",
    "windows_api_unavailable": "The Windows API is unavailable.",
    "cancelled_cleanup_refused": "{cancellation} {diagnostic} Staging is preserved at {staging}.",
    "custom_uv_config_unsupported": (
        "A custom UV_CONFIG_FILE is unsupported: move required network, index, and "
        "download settings to conventional uv configuration or documented variables "
        "before continuing."
    ),
}
