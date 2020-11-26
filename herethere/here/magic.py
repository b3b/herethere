"""here.magic"""

import asyncio

from IPython.core import magic_arguments
from IPython.core.magic_arguments import parse_argstring
from IPython.core.magic import (
    line_magic,
    magics_class,
)

from herethere.everywhere.magic import MagicEverywhere
from herethere.here import ServerConfig, start_server


@magics_class
class MagicHere(MagicEverywhere):
    """Provides the %here magic."""

    def __init__(self, shell):
        super().__init__(shell)
        self.server = None

    @line_magic("here")
    @magic_arguments.magic_arguments()
    @magic_arguments.argument(
        "config",
        nargs="?",
        default="here.env",
        help="Location of server config.",
    )
    def start_server(self, line: str):
        """Start a remote connections listener."""
        args = parse_argstring(self.start_server, line)
        config = ServerConfig.load(path=args.config, prefix="here")
        self.server = asyncio.run(start_server(config))
        print("Server started")
