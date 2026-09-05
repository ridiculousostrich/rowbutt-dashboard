#!/usr/bin/env python3
"""Rowbutt Dashboard — Root CLI entry point.

Usage:
    python3 -m cli.main agent init
    python3 -m cli.main agent start
    python3 -m cli.main aggregator pull-all
    python3 -m cli.main report --today
"""

import sys
import click

from cli.commands import agent_group, aggregator_group, report_group, web_group


@click.group()
@click.version_option(version="0.1.0", prog_name="rowbutt")
@click.option("--debug", is_flag=True, help="Enable debug logging.")
def cli(debug):
    """Rowbutt Dashboard — LLM cost monitoring & savings tracker."""
    if debug:
        import logging
        logging.basicConfig(level=logging.DEBUG)


# Register subcommand groups
cli.add_command(agent_group)
cli.add_command(aggregator_group)
cli.add_command(report_group)
cli.add_command(web_group)


def main():
    cli()


if __name__ == "__main__":
    main()
