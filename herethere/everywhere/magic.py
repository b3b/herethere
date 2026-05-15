"""herethere.everywhere.magic"""

import nest_asyncio
from IPython.core.magic import Magics
from traitlets.config.configurable import Configurable


class MagicEverywhere(Magics, Configurable):
    """Base class for magic commands."""

    def __init__(self, shell):
        super().__init__(shell)
        if shell is not None:
            nest_asyncio.apply()
