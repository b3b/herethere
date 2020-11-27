from io import StringIO
from contextlib import redirect_stdout
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_line_executed(there):
    out = StringIO()
    with redirect_stdout(out):
        await there.runcode("print('hello there')")
    assert out.getvalue() == "hello there\n"


@pytest.mark.asyncio
async def test_file_uploaded(there, tmpdir):
    await there.upload('tests/hello.txt', 'hello_remote.txt')
    with open(Path(tmpdir) / 'hello_remote.txt') as f:
        assert f.read() == 'hello\n'
