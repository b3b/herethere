import asyncio
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import asyncssh
import pytest

from herethere.there.client import (
    Client,
    ConnectionNotConfiguredError,
    PersistentConnection,
)
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


@pytest.mark.asyncio
async def test_persistent_connection_context_exit_noop():
    connection = PersistentConnection()

    assert await connection.__aexit__(None, None, None) is None


def test_persistent_connection_close_ignores_asyncssh_error(mocker):
    connection = PersistentConnection()
    ssh = mocker.Mock()
    ssh.close.side_effect = asyncssh.Error(1, "close failed")
    connection.connection = ssh

    connection.close()

    assert connection.connection is None


@pytest.mark.asyncio
async def test_persistent_connection_reconnects_after_failed_ping(mocker):
    connection = PersistentConnection()
    stale = mocker.Mock()
    stale.run = mocker.AsyncMock(side_effect=asyncssh.Error(1, "ping failed"))
    connection.connection = stale
    reconnect = mocker.patch.object(
        connection,
        "reconnect",
        new=mocker.AsyncMock(return_value="fresh"),
    )

    assert await connection.ensure_connected() == "fresh"

    reconnect.assert_awaited_once_with()
    assert connection.connection is None


def test_sftp_progress_handler_logs(mocker):
    client = Client()
    logger = mocker.patch("herethere.there.client.logger")

    client.sftp_progress_handler("src", "dst", 1, 2)

    logger.debug.assert_called_once()


class ReaderOnce:
    def __init__(self, *chunks):
        self.chunks = list(chunks)

    async def readline(self):
        return self.chunks.pop(0) if self.chunks else ""


class WriterWithoutFlush:
    def __init__(self):
        self.written = ""

    def write(self, data):
        self.written += data


class FakeProcessContext:
    def __init__(self, process):
        self.process = process

    async def __aenter__(self):
        return self.process

    async def __aexit__(self, *exc_info):
        pass


class FakeConnectionContext:
    def __init__(self, process):
        self.process = process

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        pass

    def create_process(self, command):
        self.process.command = command
        return FakeProcessContext(self.process)


@pytest.mark.asyncio
async def test_execute_code_accepts_writer_without_flush(mocker):
    process = mocker.Mock()
    process.stdin = mocker.Mock()
    process.stdout = ReaderOnce("out")
    process.stderr = ReaderOnce("")
    process.wait = mocker.AsyncMock()
    stdout = WriterWithoutFlush()

    client = Client()
    client.connection = FakeConnectionContext(process)

    await client._execute_code("code", "print('hello')", stdout=stdout)

    assert process.command == "code"
    process.stdin.write.assert_called_once_with("print('hello')")
    process.stdin.write_eof.assert_called_once_with()
    assert stdout.written == "out"
    process.wait.assert_awaited_once_with()
