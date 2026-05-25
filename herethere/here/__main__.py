"""herethere.here.__main__"""

import asyncio
import logging

import asyncssh

from .config import ServerConfig
from .server import start_server


def configure_logging():
    """Configure logging for the command-line server."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("herethere").setLevel(logging.DEBUG)
    asyncssh.set_log_level("WARNING")


async def serve():
    """Run server until interrupted."""
    server = await start_server(ServerConfig.load(prefix="here"), namespace={})
    try:
        await asyncio.Event().wait()
    finally:
        logging.getLogger(__name__).info("Stopping SSH server.")
        await server.stop()
        logging.getLogger(__name__).info("SSH server stopped.")


def main():
    """Run server here."""
    configure_logging()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
