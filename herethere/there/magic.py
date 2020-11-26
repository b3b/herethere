"""there.magic"""
import asyncio

from IPython.core import magic_arguments
from IPython.core.magic_arguments import parse_argstring
from IPython.core.magic import (
    cell_magic,
    line_magic,
    magics_class,
)


from herethere.everywhere import ConnectionConfig
from herethere.everywhere.magic import MagicEverywhere
from herethere.there import Client


@magics_class
class MagicThere(MagicEverywhere):
    """Provides the %there magic."""

    def __init__(self, shell):
        super().__init__(shell)
        self.client = Client()
        self.last_output = ""

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

    @cell_magic("there")
    def runcode(self, line, cell="") -> str:
        """Execute cell code on remote side."""
        code = "\n".join((line, cell))
        self.last_output = asyncio.run(self.client.runcode(code))
