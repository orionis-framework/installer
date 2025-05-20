import argparse
from orionis_cli.core.manager import OrionisManager

def setup() -> None:
    """
    Orionis Framework CLI Entry Point

    Handles command-line interface configuration and command dispatching for Orionis operations.
    Supports project creation, version checking, and help documentation.

    Command Structure:
        orionis [--version] [--help] <command> [<name>]

    Arguments:
        --version       Display current Orionis version
        --help          Show this help message
        command         Action to execute (currently only 'new')
        name            Name for new application (default: 'example-app')

    Examples:
        orionis new myapp       # Create new application
        orionis --version       # Show framework version
        orionis --help          # Display help information
    """
    # Initialize argument parser with enhanced settings
    parser = argparse.ArgumentParser(
        prog="orionis",
        description="Orionis Framework Command Line Interface",
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For more information, visit https://orionis-framework.com/"
    )

    # Primary command arguments
    parser.add_argument(
        'command',
        nargs='?',
        choices=['new'],
        help="create a new Orionis application"
    )

    # Application name parameter
    parser.add_argument(
        'name',
        nargs='?',
        default="example-app",
        help="name for the new application (default: %(default)s)"
    )

    # Optional flags
    parser.add_argument(
        '-v', '--version',
        action='store_true',
        help="show version information and exit"
    )
    parser.add_argument(
        '-h', '--help',
        action='store_true',
        help="show this help message and exit"
    )

    # Parse command-line arguments
    args = parser.parse_args()

    # Initialize the Orionis manager
    manager = OrionisManager()

    # Command routing logic
    if args.help:
        manager.help()
    elif args.version:
        manager.version()
    elif args.command == 'new':
        manager.new(args.name)
    else:
        parser.print_help()

# Main entry point for the script
if __name__ == "__main__":
    setup()