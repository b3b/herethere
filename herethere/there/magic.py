"""there.magic"""
import asyncio
import shlex

from IPython.core import magic_arguments
from IPython.core.magic_arguments import parse_argstring
from IPython.core.magic import (
    cell_magic,
    line_magic,
    magics_class,
)
from IPython.display import display

from herethere.everywhere import ConnectionConfig
from herethere.everywhere.magic import MagicEverywhere
from herethere.there.client import Client
from herethere.there.commands import ContextObject, NeedDisplay, there_group
from herethere.there.output import LimitedOutput


@magics_class
class MagicThere(MagicEverywhere):
    """Provides the %there magic."""

    def __init__(self, shell):
        super().__init__(shell)
        self.client = Client()

    @line_magic("connect-there")
    @magic_arguments.magic_arguments()
    @magic_arguments.argument(
        "config",
        nargs="?",
        default="there.env",
        help="Location of connection config.",
    )
    def connect(self, line):
        """Connect to remote interpreter."""
        args = parse_argstring(self.connect, line)
        config = ConnectionConfig.load(path=args.config, prefix="there")
        asyncio.run(self.client.connect(config))

    @line_magic("there")
    @cell_magic("there")
    def there(self, line, cell="") -> str:
        """Execute command on remote side."""
        # pylint: disable=too-many-function-args,unexpected-keyword-arg

        def run(obj):
            # pylint: disable=no-value-for-parameter
            there_group(
                shlex.split(line),
                "there",
                standalone_mode=False,
                obj=obj,
            )

        try:
            run(ContextObject(self.client, cell))
        except NeedDisplay as exc:
            out = LimitedOutput(maxlen=exc.maxlen)
            display(out)
            run(ContextObject(self.client, cell, stdout=out, stderr=out))
