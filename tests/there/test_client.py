from contextlib import redirect_stdout
from io import StringIO

import pytest


@pytest.mark.asyncio
async def test_line_executed(there):
    out = StringIO()
    with redirect_stdout(out):
        await there.runcode("print('hello there')")
    assert out.getvalue() == "hello there\n"


@pytest.mark.asyncio
async def test_upload(there):
    await there.upload('tests/hello.txt', 'hello_remote.txt')
