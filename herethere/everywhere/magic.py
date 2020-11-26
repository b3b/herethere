"""herethere.everywhere.magic"""

from IPython.core.magic import Magics
from traitlets.config.configurable import Configurable
import nest_asyncio


class MagicEverywhere(Magics, Configurable):
    """Base class for magic commands."""

    def __init__(self, shell):
        super().__init__(shell)
        nest_asyncio.apply()
