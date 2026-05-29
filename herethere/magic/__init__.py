"""herethere.magic"""

import herethere.there.commands.log  # noqa
from herethere.here.magic import MagicHere
from herethere.there.ai import register_ai_commands
from herethere.there.magic import MagicThere


def load_ipython_extension(ipython):
    """Hook for `%load_extension` IPython command."""
    register_ai_commands()
    ipython.register_magics(MagicHere(ipython))
    ipython.register_magics(MagicThere(ipython))
