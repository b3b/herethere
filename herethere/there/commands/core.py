"""herethere.there.commands.core"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import TextIO

import click

from herethere.everywhere.loop import run_background, run_sync
from herethere.there.client import Client
from herethere.there.history import RecentThereHistory


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
    background: bool = False
    worker: bool = False
    raw_line: str | None = None
    raw_remainder: str | None = None
    history: RecentThereHistory | None = None

    def runcode(self):
        """Execute Python code on the remote side."""
        if not self.code:
            raise EmptyCode("Code to execute is not specified.")
        # prepend with "\n" so error message line matches cell line number
        code = "# %%there ... \n" + self.code

        if self.background:
            return self._run_background_command("runcode_background", code)
        if self.worker:
            return self._run_worker_command(code)
        return self._run_command("runcode", code)

    def shell(self):
        """Execute shell command on the remote side."""
        if not self.code:
            raise EmptyCode("Code to execute is not specified.")

        if self.background:
            return self._run_background_command("shell", self.code)
        return self._run_command("shell", self.code)

    def get(self):
        """Evaluate a Python expression on the remote side."""
        if self.background:
            raise click.ClickException("get cannot be used with --background.")
        if not self.code:
            raise EmptyCode("Expression to evaluate is not specified.")
        return run_sync(self.client.get(self.code))

    def _run_command(self, command: str, code: str):
        """Execute SSH command with a code."""
        handler = getattr(self.client, command)
        run_sync(handler(code, stdout=self.stdout, stderr=self.stderr))

    def _run_worker_command(self, code: str):
        """Execute worker code synchronously and report its structured status."""
        result = run_sync(
            self.client.execute_worker(
                code,
                stdout=self.stdout,
                stderr=self.stderr,
            )
        )
        if not result.ok:
            error = result.error
            raise click.ClickException(f"{error.remote_type}: {error.message}")

    def _run_background_command(self, command: str, code: str):
        """Execute SSH command with a code, in background."""

        async def run():
            client = await self.client.copy()
            try:
                handler = getattr(client, command)
                await handler(code, stdout=self.stdout, stderr=self.stderr)
            finally:
                await client.disconnect()

        return run_background(run())


@click.group(invoke_without_command=True)
@click.option(
    "-b", "--background", is_flag=True, default=False, help="Run in background"
)
@click.option(
    "--worker",
    is_flag=True,
    default=False,
    help="Run Python on a worker thread and wait for it to finish.",
)
@click.option(
    "-l",
    "--limit",
    default=24,
    type=click.IntRange(1, 1000),
    help="Number of lines to show when in background mode",
)
@click.option(
    "-d",
    "--delay",
    type=float,
    default=0,
    help="The time to wait in seconds before executing a command",
)
@click.pass_context
def there_group(ctx, background, worker, limit, delay):
    """Group of commands to run on remote side."""
    if worker and background:
        raise click.UsageError("--worker and --background cannot be used together.")
    if worker and ctx.invoked_subcommand is not None:
        raise click.UsageError("--worker can only be used for Python code execution.")
    if background:
        if not all((ctx.obj.stdout, ctx.obj.stderr)):
            raise NeedDisplay(limit)
        ctx.obj.background = True
    if worker:
        ctx.obj.worker = True
    if delay:
        time.sleep(delay)
    if ctx.invoked_subcommand is None:
        # Execute Python code if no command specified
        if ctx.obj.history is not None and ctx.obj.code:
            ctx.obj.history.remember(line=ctx.obj.raw_line or "", cell=ctx.obj.code)
        return ctx.obj.runcode()
    return None


@there_group.command()
@click.pass_context
def shell(ctx):
    """Execute shell command on remote side."""
    return ctx.obj.shell()


def there_raw_remainder(func):
    """Decorator for Click commands that consume raw text after their name.

    IPython magic lines are parsed with ``shlex.split()`` before Click sees
    them so normal commands can accept quoted paths like ``"test data.csv"``.
    That shell-style parsing removes quotes which may be meaningful Python
    source for expression commands, for example ``result["latest one"]``.

    Decorate commands which interpret their arguments as source text. The
    command still receives normal Click arguments, but ``ctx.obj.raw_remainder``
    contains the original unparsed text after the command name when the command
    was invoked from magic.
    """

    @wraps(func)
    def _wrapper(*args, **kwargs):
        ctx = click.get_current_context()
        ctx.obj.raw_remainder = raw_remainder_after_command(ctx)
        return func(*args, **kwargs)

    return _wrapper


@there_group.command("get", context_settings={"ignore_unknown_options": True})
@there_raw_remainder
@click.pass_context
@click.argument("expression", nargs=-1, type=click.UNPROCESSED)
def get_value(ctx, expression):
    """Evaluate expression on remote side and return its value."""
    if expression:
        ctx.obj.code = ctx.obj.raw_remainder or " ".join(expression)
    return ctx.obj.get()


def raw_remainder_after_command(ctx: click.Context) -> str:
    """Return raw text after the current command name in the magic line."""
    line = getattr(ctx.obj, "raw_line", None)
    if not line or not ctx.info_name:
        return ""

    pattern = rf"(?<!\S){re.escape(ctx.info_name)}(?!\S)"
    match = re.search(pattern, line)
    if not match:
        return ""

    return line[match.end() :].lstrip()


@there_group.command()
@click.pass_context
@click.argument("paths", nargs=-1, required=True, metavar="LOCAL_PATH... [REMOTE_PATH]")
def upload(ctx, paths):
    """Upload files and directories.

    With one path, upload to the current remote SFTP directory. With multiple
    paths, the last path is the remote destination.
    """
    if len(paths) == 1:
        localpaths = paths
        remotepath = "."
    else:
        localpaths = paths[:-1]
        remotepath = paths[-1]

    path_type = click.Path(exists=True)
    localpaths = tuple(path_type.convert(path, None, ctx) for path in localpaths)

    if len(localpaths) == 1:
        localpaths = localpaths[0]
    return run_sync(ctx.obj.client.upload(localpaths, remotepath))


@there_group.command()
@click.pass_context
@click.argument("paths", nargs=-1, required=True, metavar="REMOTE_PATH... [LOCAL_PATH]")
def download(ctx, paths):
    """Download files and directories.

    With one path, download to the current local directory. With multiple
    paths, the last path is the local destination.
    """
    if len(paths) == 1:
        remotepaths = paths
        localpath = "."
    else:
        remotepaths = paths[:-1]
        localpath = paths[-1]

    if len(remotepaths) == 1:
        remotepaths = remotepaths[0]
    return run_sync(ctx.obj.client.download(remotepaths, localpath))


def there_code_shortcut(
    handler: Callable[[str], str],
) -> Callable[[click.Context], None]:
    """Decorator to register %there subcommand to execute Python code.

    :param handler:
        a function, that receives text from the Jupyter cell,
        and returns Python code to execute on the remote side
    """

    @there_group.command(handler.__name__)
    @click.pass_context
    @wraps(handler)
    def _wrapper(ctx, *args, **kwargs):
        ctx.obj.code = handler(ctx.obj.code, *args, **kwargs)
        return ctx.obj.runcode()

    _wrapper.__click_params__ = getattr(handler, "__click_params__", [])

    return _wrapper
