import asyncio
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from herethere.there.client import Client, ConnectionNotConfiguredError
from herethere.there.commands.log import LOG_COMMAND_TEMPLATE


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
async def test_output_streamed_before_command_exits(there):
    out = StringIO()
    task = asyncio.create_task(there.shell("printf 'started\\n'; sleep 30", stdout=out))

    try:
        # Protect long-running commands: output must be forwarded before the
        # remote process exits, not buffered until completion.
        for _ in range(50):
            if out.getvalue() == "started\n":
                break
            await asyncio.sleep(0.02)

        assert out.getvalue() == "started\n"
        assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_python_output_streamed_before_code_exits(there):
    out = StringIO()
    task = asyncio.create_task(
        there.runcode(
            "import sys, time\n"
            "sys.stdout.write('started\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(1)\n",
            stdout=out,
        )
    )

    for _ in range(50):
        if out.getvalue() == "started\n":
            break
        await asyncio.sleep(0.02)

    assert out.getvalue() == "started\n"
    assert not task.done()
    await task


@pytest.mark.asyncio
async def test_log_command_streams_logging_record(there):
    out = StringIO()
    marker = "herethere-log-stream-test"
    task = asyncio.create_task(
        there.runcode_background(LOG_COMMAND_TEMPLATE, stdout=out)
    )
    trigger = await there.copy()

    try:
        # The log listener is long-running. Use a second client to emit remote
        # records and verify the listener streams them back immediately.
        for _ in range(50):
            await trigger.runcode(f"import logging\nlogging.warning({marker!r})\n")
            if marker in out.getvalue():
                break
            await asyncio.sleep(0.02)

        assert "[WARNING]" in out.getvalue()
        assert marker in out.getvalue()
    finally:
        await trigger.runcode("ssh_server_closed.set()")
        await trigger.disconnect()
        await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_log_command_cancellation_returns_quickly(there):
    out = StringIO()
    task = asyncio.create_task(
        there.runcode_background(LOG_COMMAND_TEMPLATE, stdout=out)
    )

    await asyncio.sleep(0.1)
    task.cancel()

    # Cancellation cleanup should not block indefinitely on the remote channel.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)


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
    try:
        assert connection.connection.config == there.connection.config
    finally:
        await connection.disconnect()


@pytest.mark.asyncio
async def test_connection_disconnected(there):
    assert there.connection.connection
    await there.disconnect()
    assert not there.connection.connection


@pytest.mark.asyncio
async def test_exception_on_unconfigured_connection_copy():
    client = Client()
    with pytest.raises(
        ConnectionNotConfiguredError, match="Connection is not configured."
    ):
        await client.copy()
