"""herethere.there.commands"""
import asyncio
from dataclasses import dataclass

import click

from herethere.there.client import Client


@dataclass
class ContextObject:
    """Context to pass to `there` group commands."""

    client: Client
    code: str


@click.group(invoke_without_command=True)
@click.pass_context
def there_group(ctx):
    """Group of commands to run on remote side."""
    if ctx.invoked_subcommand is None:
        # Execute python code if no command specified
        asyncio.run(ctx.obj.client.runcode(ctx.obj.code))


@there_group.command()
@click.pass_context
def shell(ctx):
    """Execute shell command on remote side."""
    asyncio.run(ctx.obj.client.shell(ctx.obj.code))


@there_group.command()
@click.pass_context
@click.argument("localpaths", type=click.Path(exists=True), nargs=-1, required=True)
@click.argument("remotepath", nargs=1)
def upload(ctx, localpaths, remotepath):
    """Upload files and directories to `remotepath`."""
    if len(localpaths) == 1:
        localpaths = localpaths[0]
    asyncio.run(ctx.obj.client.upload(localpaths, remotepath))
