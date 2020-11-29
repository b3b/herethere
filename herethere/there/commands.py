"""herethere.there.commands"""
import asyncio
from dataclasses import dataclass
from typing import TextIO

import click

from herethere.there.client import Client


class EmptyCode(Exception):
    """Command was started without code."""


class NeedDisplay(Exception):
    """Background command was started without display."""

    def __init__(self, maxlen: int):
        self.maxlen = maxlen
        super().__init__("Display required.")


@dataclass
class ContextObject:
    """Context to pass to `there` group commands."""

    client: Client
    code: str
    stdout: TextIO = None
    stderr: TextIO = None

    def runcode(self):
        """Execute python code on the remote side."""
        if not self.code:
            raise EmptyCode("Code to execute is not specified.")
        if self.stdout:
            asyncio.create_task(
                self.client.runcode_background(
                    self.code, stdout=self.stdout, stderr=self.stderr
                )
            )
        else:
            asyncio.run(self.client.runcode(self.code))

    def shell(self):
        """Execute shell command on the remote side."""
        if not self.code:
            raise EmptyCode("Code to execute is not specified.")
        if self.stdout:
            asyncio.create_task(
                self.client.shell(self.code, stdout=self.stdout, stderr=self.stderr)
            )
        else:
            asyncio.run(self.client.shell(self.code))


@click.group(invoke_without_command=True)
@click.option(
    "-b", "--background", is_flag=True, default=False, help="Run in background"
)
@click.option(
    "-l",
    "--limit",
    default=24,
    type=click.IntRange(1, 1000),
    help="Number of lines to show when in background mode",
)
@click.pass_context
def there_group(ctx, background, limit):
    """Group of commands to run on remote side."""
    if background and not all((ctx.obj.stdout, ctx.obj.stderr)):
        raise NeedDisplay(limit)
    if ctx.invoked_subcommand is None:
        # Execute python code if no command specified
        ctx.obj.runcode()


@there_group.command()
@click.pass_context
def shell(ctx):
    """Execute shell command on remote side."""
    ctx.obj.shell()


@there_group.command()
@click.pass_context
@click.argument("localpaths", type=click.Path(exists=True), nargs=-1, required=True)
@click.argument("remotepath", nargs=1)
def upload(ctx, localpaths, remotepath):
    """Upload files and directories to `remotepath`."""
    if len(localpaths) == 1:
        localpaths = localpaths[0]
    asyncio.run(ctx.obj.client.upload(localpaths, remotepath))
