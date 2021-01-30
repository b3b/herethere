from io import StringIO
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from herethere.there.client import Client


@pytest.mark.asyncio
async def test_line_executed(there):
    out = StringIO()
    with redirect_stdout(out):
        await there.runcode("print('hello there')")
    assert out.getvalue() == "hello there\n"


@pytest.mark.asyncio
async def test_line_executed_with_output_redirection(there):
    out = StringIO()
    err_out = StringIO()
    await there.runcode(
        "import sys; sys.stdout.write('hello'); sys.stderr.write('there')",
        stdout=out,
        stderr=err_out,
    )
    assert out.getvalue() == "hello"
    assert err_out.getvalue() == "there"


@pytest.mark.asyncio
async def test_background_line_executed(there):
    out = StringIO()
    with redirect_stdout(out):
        await there.runcode_background("print('hello there')")
    assert out.getvalue() == "hello there\n"


@pytest.mark.asyncio
async def test_shell_command_executed(there):
    out = StringIO()
    with redirect_stdout(out):
        await there.shell("echo hello there")
    assert out.getvalue() == "hello there\n"


@pytest.mark.asyncio
async def test_file_uploaded(there, tmpdir):
    await there.upload("tests/hello.txt", "hello_remote.txt")
    with open(Path(tmpdir) / "hello_remote.txt") as f:
        assert f.read() == "hello\n"


@pytest.mark.asyncio
async def test_connection_copied(there):
    connection = await there.copy()
    assert connection.connection.config == there.connection.config


@pytest.mark.asyncio
async def test_connection_disconnected(there):
    assert there.connection.connection
    await there.disconnect()
    assert not there.connection.connection


@pytest.mark.asyncio
async def test_exception_on_unconfigured_connection_copy():
    client = Client()
    with pytest.raises(Exception, match="Connection is not configured."):
        await client.copy()
