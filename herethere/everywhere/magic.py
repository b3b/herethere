"""herethere.everywhere.magic"""

from IPython.core.interactiveshell import InteractiveShell
from IPython.core.magic import Magics
from traitlets.config.configurable import Configurable


def _patch_ipython_loop_if_needed(shell):
    """Patch IPython's running loop for sync magic methods."""
    if not isinstance(shell, InteractiveShell):
        return

    import nest_asyncio2  # noqa: PLC0415

    nest_asyncio2.apply()


class MagicEverywhere(Magics, Configurable):
    """Base class for magic commands."""

    def __init__(self, shell):
        super().__init__(shell)
        _patch_ipython_loop_if_needed(shell)
