"""Fast-food voice ordering agent."""


def main() -> None:
    """Run the command-line entry point."""
    from .cli_chat import main as cli_main

    cli_main()
