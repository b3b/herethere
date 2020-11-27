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
from herethere.there.client import Client
from herethere.there.commands import ContextObject, there_group


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

    @cell_magic("there")
    def there(self, line, cell="") -> str:
        """Execute command on remote side."""
        # pylint: disable=too-many-function-args,unexpected-keyword-arg
        there_group(
            line.split(),
            "there",
            standalone_mode=False,
            obj=ContextObject(self.client, cell),
        )
