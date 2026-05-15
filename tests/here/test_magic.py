import pytest

from herethere.magic import MagicHere


@pytest.mark.asyncio
async def test_server_is_started(event_loop, tmp_environ):
    tmp_environ["HERE_PORT"] = "0"
    magic = MagicHere(shell=None)
    magic.start_server("tests/here.env")

    server = magic.server
    assert server.is_serving()
    await server.stop()
