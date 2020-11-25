"""herethere.here.__main__"""

import asyncio

from .config import ServerConfig
from .server import start_server


def main():
    """Run server here."""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_server(ServerConfig.load(prefix="here")))
    loop.run_forever()


main()
