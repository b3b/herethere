"""herethere.magic"""
from herethere.here.magic import MagicHere


def load_ipython_extension(ipython):
    """Hook for `%load_extension` IPython command."""
    ipython.register_magics(MagicHere(ipython))
