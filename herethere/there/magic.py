"""there.magic"""

import shlex
from concurrent.futures import Future

from IPython.core import magic_arguments
from IPython.core.magic import (
    cell_magic,
    line_magic,
    magics_class,
)
from IPython.core.magic_arguments import parse_argstring
from IPython.display import display

from herethere.everywhere import ConnectionConfig
from herethere.everywhere.logging import logger
from herethere.everywhere.loop import run_sync
from herethere.everywhere.magic import MagicEverywhere
from herethere.there.client import Client
from herethere.there.commands import (
    ContextObject,
    NeedDisplay,
    there_group,
)
from herethere.there.output import LimitedOutput


@magics_class
class MagicThere(MagicEverywhere):
    """Provides the %there magic."""

    def __init__(self, shell):
        super().__init__(shell)
        self.client = Client()
        self.background_futures: set[Future] = set()

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
        run_sync(self.client.connect(config))

    @line_magic("there")
    @cell_magic("there")
    def there(self, line, cell=""):
        """Execute command on remote side."""
        # pylint: disable=too-many-function-args,unexpected-keyword-arg
        args = shlex.split(line)

        def run(obj):
            # pylint: disable=no-value-for-parameter
            return there_group(
                args,
                "there",
                standalone_mode=False,
                obj=obj,
            )

        try:
            future = run(ContextObject(self.client, cell, raw_line=line))
        except NeedDisplay as exc:
            out = LimitedOutput(maxlen=exc.maxlen)
            display(out)
            future = run(
                ContextObject(
                    self.client,
                    cell,
                    stdout=out,
                    stderr=out,
                    raw_line=line,
                )
            )

        self._observe_background_future(future)
        if isinstance(future, Future):
            return None
        return future

    def _observe_background_future(self, future):
        """Track fire-and-forget background work and log failures."""
        if not isinstance(future, Future):
            return

        self.background_futures.add(future)

        def done(completed):
            self.background_futures.discard(completed)
            if completed.cancelled():
                return
            exc = completed.exception()
            if exc is None:
                return
            logger.error(
                "Background %%there command failed.",
                exc_info=(type(exc), exc, exc.__traceback__),
            )

        future.add_done_callback(done)
