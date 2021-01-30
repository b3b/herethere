"""herethere.here.__main__"""

import logging

import asyncio
import asyncssh

from .config import ServerConfig
from .server import start_server


def main():
    """Run server here."""

    for logger in [logging.getLogger(name) for name in logging.root.manager.loggerDict]:
        logger.setLevel(logging.DEBUG)

    logging.basicConfig(level=logging.DEBUG)
    asyncssh.set_log_level("DEBUG")

    loop = asyncio.get_event_loop()
    loop.run_until_complete(
        start_server(ServerConfig.load(prefix="here"), namespace={})
    )
    loop.run_forever()


if __name__ == "__main__":
    main()
