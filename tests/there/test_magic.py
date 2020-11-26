from contextlib import redirect_stdout
from io import StringIO

import pytest

from herethere.magic import MagicThere


@pytest.fixture
async def connected_there(server_instance):
    magic = MagicThere(shell=None)
    magic.connect("tests/there.env")
    assert magic.client.connection
    yield magic


@pytest.mark.asyncio
async def test_connected(server_instance, tmp_environ):
    magic = MagicThere(shell=None)
    magic.connect("tests/there.env")
    assert magic.client.connection


@pytest.mark.asyncio
async def test_code_executed(connected_there):
    out = StringIO()
    with redirect_stdout(out):
        connected_there.runcode("print('hello')")
    assert out.getvalue() == "hello\n"
