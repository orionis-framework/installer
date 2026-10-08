class InstallerError(Exception):
    """Represent a critical creation failure with exit code 1."""

    exit_code = 1

class ValidationError(InstallerError):
    """Represent invalid arguments or an unmet contract with exit code 2."""

    exit_code = 2

class CompatibilityError(ValidationError):
    """Identify a skeleton or framework compatibility contract violation."""

class PrerequisiteError(ValidationError):
    """Identify an unavailable executable or unsupported Python prerequisite."""

class ProcessError(InstallerError):
    """Report a controlled process failure without exposing captured output."""

class Cancelled(InstallerError):
    """Represent explicit user cancellation with exit code 130."""

    exit_code = 130
