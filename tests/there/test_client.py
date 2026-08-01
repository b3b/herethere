import asyncio
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import asyncssh
import pytest

from herethere.everywhere.commands import (
    BACKGROUND_EXECUTE_COMMAND,
    BACKGROUND_VALUE_COMMAND,
    PING_COMMAND,
    RECENT_LOGS_COMMAND,
    SHELL_COMMAND,
)
from herethere.everywhere.shell import ShellResult
from herethere.everywhere.values import RemoteValueError, dumps_error, dumps_value
from herethere.there.client import (
    Client,
    ConnectionNotConfiguredError,
    PersistentConnection,
    ProtocolError,
    ProtocolVersionError,
)
from herethere.there.commands.log import LOG_COMMAND_TEMPLATE


@pytest.mark.asyncio
async def test_repeated_ping_keeps_server_available(there):
    assert await there.ping() == "pong"
    assert await there.ping() == "pong"
    assert await there.get("6 * 7") == 42


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
async def test_structured_shell_returns_nonzero_and_separates_streams(there):
    out = StringIO()
    err = StringIO()

    result = await there.execute_shell(
        "printf out; printf err >&2; exit 7",
        stdout=out,
        stderr=err,
    )

    assert result.returncode == 7
    assert not result.ok
    assert out.getvalue() == "out"
    assert err.getvalue() == "err"


@pytest.mark.asyncio
async def test_get_returns_simple_value(there):
    await there.runcode("x = 41")

    assert await there.get("x + 1") == 42


@pytest.mark.asyncio
async def test_get_ignores_remote_expression_stdout(there):
    assert await there.get("print('noisy') or 1") == 1


@pytest.mark.asyncio
async def test_get_returns_nested_value(there):
    await there.runcode(
        "data = {\n"
        "    'numbers': [1, 2, 3],\n"
        "    'shape': (2, 3),\n"
        "    'meta': {'ok': True},\n"
        "}\n"
    )

    assert await there.get("data") == {
        "numbers": [1, 2, 3],
        "shape": (2, 3),
        "meta": {"ok": True},
    }


@pytest.mark.asyncio
async def test_get_remote_error(there):
    with pytest.raises(RemoteValueError, match="NameError"):
        await there.get("missing_name")


@pytest.mark.asyncio
async def test_get_awaits_coroutine(there):
    await there.runcode("async def answer():\n    return 42\n")

    assert await there.get("answer()") == 42


@pytest.mark.asyncio
async def test_structured_execute_and_get_share_live_namespace(there):
    out = StringIO()
    err = StringIO()

    execution = await there.execute(
        "live_cli_value = 40\nprint('created')",
        stdout=out,
        stderr=err,
    )
    value = await there.get("live_cli_value + 2")

    assert execution.ok
    assert execution.error is None
    assert out.getvalue() == "created\n"
    assert err.getvalue() == ""
    assert value == 42


@pytest.mark.asyncio
async def test_background_execute_and_get_share_live_namespace(there):
    out = StringIO()
    err = StringIO()

    execution = await there.execute_background(
        "background_client_value = 40\nprint('created')",
        stdout=out,
        stderr=err,
    )
    value = await there.get_background("background_client_value + 2")

    assert execution.ok
    assert out.getvalue() == ""
    assert err.getvalue() == ""
    assert value == 42


@pytest.mark.asyncio
async def test_structured_execute_returns_remote_error(there):
    err = StringIO()

    execution = await there.execute("raise KeyError('bad')", stderr=err)
    assert not execution.ok
    assert execution.error.remote_type == "KeyError"
    assert "KeyError" in execution.error.traceback
    assert "KeyError" in err.getvalue()


@pytest.mark.asyncio
async def test_file_uploaded(there, tmpdir):
    await there.upload("tests/hello.txt", "hello_remote.txt")
    with open(Path(tmpdir) / "hello_remote.txt") as f:
        assert f.read() == "hello\n"


@pytest.mark.asyncio
async def test_file_downloaded(there, tmpdir):
    remote_path = Path(tmpdir) / "hello_remote.txt"
    local_path = Path(tmpdir) / "hello_local.txt"
    remote_path.write_text("hello remote\n")

    await there.download("hello_remote.txt", local_path)

    assert local_path.read_text() == "hello remote\n"


@pytest.mark.asyncio
async def test_directory_downloaded(there, tmpdir):
    remote_path = Path(tmpdir) / "remote_dir"
    local_path = Path(tmpdir) / "local_dir"
    remote_path.mkdir()
    (remote_path / "hello.txt").write_text("hello remote\n")

    await there.download("remote_dir", local_path)

    assert (local_path / "hello.txt").read_text() == "hello remote\n"


@pytest.mark.asyncio
async def test_connection_copied(there):
    there.structured_protocol = True
    connection = await there.copy()
    try:
        assert connection.connection.config == there.connection.config
        assert connection.structured_protocol is True
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
    stale.create_process.side_effect = asyncssh.Error(1, "ping failed")
    connection.connection = stale
    reconnect = mocker.patch.object(
        connection,
        "reconnect",
        new=mocker.AsyncMock(return_value="fresh"),
    )

    assert await connection.ensure_connected() == "fresh"

    reconnect.assert_awaited_once_with()
    assert connection.connection is None


@pytest.mark.asyncio
async def test_persistent_connection_reconnects_after_invalid_ping(mocker):
    process = mocker.Mock()
    process.stdout = ReaderOnce(b"wrong")
    process.stderr = ReaderOnce(b"")
    process.wait = mocker.AsyncMock(return_value=mocker.Mock(returncode=0))
    connection = PersistentConnection()
    connection.connection = FakeConnectionContext(process)
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

    logger.debug.assert_called_once_with(
        "SFTP progress: %s -> %s: %s/%s bytes (%.1f%%)",
        "src",
        "dst",
        1,
        2,
        50.0,
    )


class ReaderOnce:
    def __init__(self, *chunks):
        self.chunks = list(chunks)

    async def read(self):
        return self.chunks.pop(0) if self.chunks else ""

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

    def close(self):
        pass

    def create_process(self, command, **kwargs):
        self.process.command = command
        self.process.process_options = kwargs
        return FakeProcessContext(self.process)


def protocol_process(mocker, stdout, stderr=""):
    process = mocker.Mock()
    process.stdin = mocker.Mock()
    process.stdout = ReaderOnce(*stdout)
    process.stderr = ReaderOnce(stderr)
    process.wait = mocker.AsyncMock()
    return process


@pytest.mark.asyncio
async def test_client_ping_sends_shared_command_and_returns_pong(mocker):
    process = mocker.Mock()
    process.stdout = ReaderOnce(b"pong")
    process.stderr = ReaderOnce(b"")
    process.wait = mocker.AsyncMock(return_value=mocker.Mock(returncode=0))
    client = Client()
    client.connection.connection = FakeConnectionContext(process)

    assert await client.ping() == "pong"

    assert process.command == PING_COMMAND
    assert process.process_options == {"encoding": None}
    process.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_ping_reconnects_when_connection_is_missing(mocker):
    process = mocker.Mock()
    process.stdout = ReaderOnce(b"pong")
    process.stderr = ReaderOnce(b"")
    process.wait = mocker.AsyncMock(return_value=mocker.Mock(returncode=0))
    ssh = FakeConnectionContext(process)
    connection = PersistentConnection()
    reconnect = mocker.patch.object(
        connection,
        "reconnect",
        new=mocker.AsyncMock(return_value=ssh),
    )

    assert await connection.ping() == "pong"

    reconnect.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode"),
    [
        (b"", b"", 0),
        (b"pon", b"", 0),
        (b"pong\n", b"", 0),
        (b"\xff", b"", 0),
        (b"pong", b"\xff", 0),
        (b"pong", b"", 1),
    ],
)
@pytest.mark.asyncio
async def test_ping_rejects_invalid_response(mocker, stdout, stderr, returncode):
    process = mocker.Mock()
    process.stdout = ReaderOnce(stdout)
    process.stderr = ReaderOnce(stderr)
    process.wait = mocker.AsyncMock(return_value=mocker.Mock(returncode=returncode))
    connection = PersistentConnection()
    connection.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolError, match="invalid ping response"):
        await connection.ping()


@pytest.mark.parametrize("terminate_error", [None, OSError("channel closed")])
@pytest.mark.asyncio
async def test_ping_cancellation_terminates_channel_and_waits(mocker, terminate_error):
    class BlockingReader:
        async def read(self):
            await asyncio.Event().wait()

    process = mocker.Mock()
    process.stdout = BlockingReader()
    process.stderr = BlockingReader()
    process.wait = mocker.AsyncMock()
    process.terminate.side_effect = terminate_error
    connection = PersistentConnection()
    connection.connection = FakeConnectionContext(process)

    task = asyncio.create_task(connection.ping())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    process.terminate.assert_called_once_with()
    process.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_execute_code_accepts_writer_without_flush(mocker):
    process = mocker.Mock()
    process.stdin = mocker.Mock()
    process.stdout = ReaderOnce("out")
    process.stderr = ReaderOnce("")
    process.wait = mocker.AsyncMock()
    stdout = WriterWithoutFlush()
    stderr = WriterWithoutFlush()

    client = Client()
    client.connection = FakeConnectionContext(process)

    await client._execute_code(
        "code",
        "print('hello')",
        stdout=stdout,
        stderr=stderr,
    )

    assert process.command == "code"
    process.stdin.write.assert_called_once_with("print('hello')")
    process.stdin.write_eof.assert_called_once_with()
    assert stdout.written == "out"
    process.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_get_uses_value_command_and_deserializes_result(mocker):
    process = mocker.Mock()
    process.stdin = mocker.Mock()
    process.stdout = ReaderOnce(dumps_value({"ok": True}) + "\n")
    process.wait = mocker.AsyncMock()

    client = Client()
    client.connection = FakeConnectionContext(process)

    assert await client.get("{'ok': True}") == {"ok": True}
    assert process.command == "value"
    process.stdin.write.assert_called_once_with("{'ok': True}")
    process.stdin.write_eof.assert_called_once_with()
    process.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_get_background_uses_versioned_command(mocker):
    process = mocker.Mock()
    process.stdin = mocker.Mock()
    process.stdout = ReaderOnce(dumps_value(42) + "\n")
    process.wait = mocker.AsyncMock()

    client = Client()
    client.connection = FakeConnectionContext(process)

    assert await client.get_background("40 + 2") == 42
    assert process.command == BACKGROUND_VALUE_COMMAND


@pytest.mark.asyncio
async def test_get_background_detects_old_server(mocker):
    process = mocker.Mock()
    process.stdin = mocker.Mock()
    process.stdout = ReaderOnce("")
    process.stderr = ReaderOnce("Unknown command")
    process.wait = mocker.AsyncMock()

    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolVersionError, match="background execution"):
        await client.get_background("42")


@pytest.mark.asyncio
async def test_get_raises_remote_value_error(mocker):
    process = mocker.Mock()
    process.stdin = mocker.Mock()
    process.stdout = ReaderOnce(dumps_error(NameError("missing"), "Traceback") + "\n")
    process.wait = mocker.AsyncMock()

    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(RemoteValueError, match="NameError"):
        await client.get("missing")


@pytest.mark.asyncio
async def test_get_raises_when_value_command_returns_no_output(mocker):
    process = mocker.Mock()
    process.stdin = mocker.Mock()
    process.stdout = ReaderOnce("")
    process.stderr = mocker.Mock()
    process.stderr.read = mocker.AsyncMock(return_value="")
    process.wait = mocker.AsyncMock()

    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(RuntimeError, match="returned no output"):
        await client.get("1 + 1")


@pytest.mark.asyncio
async def test_get_includes_stderr_when_value_command_returns_no_output(mocker):
    process = mocker.Mock()
    process.stdin = mocker.Mock()
    process.stdout = ReaderOnce("")
    process.stderr = mocker.Mock()
    process.stderr.read = mocker.AsyncMock(return_value="remote failure")
    process.wait = mocker.AsyncMock()

    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(RuntimeError, match="remote failure"):
        await client.get("1 + 1")


@pytest.mark.asyncio
async def test_structured_client_decodes_streams_and_result(mocker):
    process = protocol_process(
        mocker,
        [
            '{"type":"stream","stream":"stdout","data":"out"}\n',
            '{"type":"stream","stream":"stderr","data":"err"}\n',
            '{"type":"result","ok":true}\n',
        ],
    )
    client = Client()
    client.connection = FakeConnectionContext(process)
    stdout = WriterWithoutFlush()
    stderr = WriterWithoutFlush()

    result = await client.execute("pass", stdout=stdout, stderr=stderr)

    assert result.ok
    assert client.structured_protocol is True
    assert stdout.written == "out"
    assert stderr.written == "err"


@pytest.mark.asyncio
async def test_structured_client_detects_old_server(mocker):
    process = protocol_process(mocker, [], "Unknown command")
    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolVersionError, match="Upgrade"):
        await client.execute("pass")
    assert client.structured_protocol is False


@pytest.mark.asyncio
async def test_background_structured_client_uses_versioned_command(mocker):
    process = protocol_process(mocker, ['{"type":"result","ok":true}\n'])
    client = Client()
    client.connection = FakeConnectionContext(process)

    result = await client.execute_background("pass")

    assert result.ok
    assert process.command == BACKGROUND_EXECUTE_COMMAND


@pytest.mark.asyncio
async def test_background_structured_old_server_keeps_foreground_cache(mocker):
    process = protocol_process(mocker, [], "Unknown command")
    client = Client()
    client.structured_protocol = True
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolVersionError, match="background execution"):
        await client.execute_background("pass")

    assert client.structured_protocol is True


@pytest.mark.asyncio
async def test_structured_client_rejects_missing_result(mocker):
    process = protocol_process(mocker, [], "diagnostic")
    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolError, match="no result"):
        await client.execute("pass")


@pytest.mark.asyncio
async def test_runcode_falls_back_to_legacy_protocol(mocker):
    client = Client()
    structured = mocker.patch.object(
        client,
        "execute",
        new=mocker.AsyncMock(side_effect=ProtocolVersionError("old")),
    )
    legacy = mocker.patch.object(client, "_execute_code", new=mocker.AsyncMock())

    await client.runcode("print('hello')", stdout="out", stderr="err")

    structured.assert_awaited_once_with("print('hello')", "out", "err")
    legacy.assert_awaited_once_with("code", "print('hello')", "out", "err")


@pytest.mark.asyncio
async def test_cached_legacy_protocol_skips_structured_attempt(mocker):
    client = Client()
    client.structured_protocol = False
    structured = mocker.patch.object(
        client,
        "_structured_operation",
        new=mocker.AsyncMock(),
    )
    with pytest.raises(ProtocolVersionError, match="Upgrade"):
        await client.execute("pass")

    structured.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lines", "message"),
    [
        (["not-json\n"], "malformed"),
        (["null\n"], "non-object"),
        (["[]\n"], "non-object"),
        (
            ['{"type":"stream","stream":"stdout","data":1}\n'],
            "invalid stream",
        ),
        (['{"type":"progress"}\n'], "unknown protocol"),
        (
            [
                '{"type":"result","ok":true}\n',
                '{"type":"result","ok":true}\n',
            ],
            "multiple result",
        ),
        (['{"type":"result","ok":"yes"}\n'], "valid status"),
    ],
)
async def test_structured_client_rejects_invalid_events(mocker, lines, message):
    process = protocol_process(mocker, lines)
    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolError, match=message):
        await client.execute("pass")


@pytest.mark.asyncio
async def test_structured_client_rejects_failure_without_details(mocker):
    process = protocol_process(mocker, ['{"type":"result","ok":false}\n'])
    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolError, match="no error details"):
        await client.execute("pass")


@pytest.mark.asyncio
async def test_structured_client_forwards_protocol_diagnostics(mocker):
    process = protocol_process(
        mocker,
        ['{"type":"result","ok":true}\n'],
        "diagnostic",
    )
    client = Client()
    client.connection = FakeConnectionContext(process)
    stderr = WriterWithoutFlush()

    result = await client.execute("pass", stderr=stderr)

    assert result.ok
    assert stderr.written == "diagnostic"


@pytest.mark.asyncio
async def test_structured_client_flushes_protocol_diagnostics(mocker):
    process = protocol_process(
        mocker,
        ['{"type":"result","ok":true}\n'],
        "diagnostic",
    )
    client = Client()
    client.connection = FakeConnectionContext(process)
    stderr = mocker.Mock()

    result = await client.execute("pass", stderr=stderr)

    assert result.ok
    stderr.write.assert_called_once_with("diagnostic")
    stderr.flush.assert_called_once_with()


@pytest.mark.asyncio
async def test_structured_client_cancellation_terminates_process(mocker):
    class BlockingReader:
        async def readline(self):
            await asyncio.Event().wait()

    process = mocker.Mock()
    process.stdin = mocker.Mock()
    process.stdout = BlockingReader()
    process.stderr = BlockingReader()
    process.wait = mocker.AsyncMock()
    client = Client()
    client.connection = FakeConnectionContext(process)

    task = asyncio.create_task(client.execute("pass"))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    process.terminate.assert_called_once_with()
    process.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_logs_decodes_finite_snapshot(mocker):
    process = protocol_process(
        mocker,
        [
            '{"type":"recent-logs","version":1,"text":"one\\ntwo\\n",'
            '"bytes":8,"records":2,"truncated":false}\n'
        ],
    )
    client = Client()
    client.connection = FakeConnectionContext(process)

    snapshot = await client.logs()

    assert process.command == RECENT_LOGS_COMMAND
    process.stdin.write.assert_called_once_with('{"version":1,"records":null}')
    process.stdin.write_eof.assert_called_once_with()
    assert snapshot.text == "one\ntwo\n"
    assert snapshot.bytes == 8
    assert snapshot.records == 2
    assert snapshot.truncated is False


@pytest.mark.asyncio
async def test_logs_requests_newest_record_count(mocker):
    process = protocol_process(
        mocker,
        [
            '{"type":"recent-logs","version":1,"text":"two\\n",'
            '"bytes":4,"records":1,"truncated":false}\n'
        ],
    )
    client = Client()
    client.connection = FakeConnectionContext(process)

    snapshot = await client.logs(max_records=1)

    process.stdin.write.assert_called_once_with('{"version":1,"records":1}')
    assert snapshot.records == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("max_records", [0, 1001, True, "1"])
async def test_logs_validates_requested_record_count(max_records):
    client = Client()

    with pytest.raises(ValueError, match="range 1..1000"):
        await client.logs(max_records=max_records)


@pytest.mark.asyncio
async def test_logs_detects_old_server(mocker):
    process = protocol_process(mocker, [], "Unknown command")
    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolVersionError, match="recent-log snapshots"):
        await client.logs()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stdout", "stderr", "message"),
    [
        ([], "", "no output"),
        ([], "remote failure", "Remote stderr"),
        (["not-json"], "", "malformed"),
        (["null"], "", "invalid recent-log data"),
        (['{"type":"other"}'], "", "invalid recent-log data"),
        (
            ['{"type":"recent-logs","version":2}'],
            "",
            "invalid recent-log data",
        ),
        (
            [
                '{"type":"recent-logs","version":1,"text":"","bytes":0,'
                '"records":0,"truncated":false}'
            ],
            "diagnostic",
            "logs command failed",
        ),
    ],
)
async def test_logs_rejects_remote_failures(mocker, stdout, stderr, message):
    process = protocol_process(mocker, stdout, stderr)
    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolError, match=message):
        await client.logs()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        {
            "type": "recent-logs",
            "version": 1,
            "text": 1,
            "bytes": 0,
            "records": 0,
            "truncated": False,
        },
        {
            "type": "recent-logs",
            "version": 1,
            "text": "",
            "bytes": "",
            "records": 0,
            "truncated": False,
        },
        {
            "type": "recent-logs",
            "version": 1,
            "text": "",
            "bytes": True,
            "records": 0,
            "truncated": False,
        },
        {
            "type": "recent-logs",
            "version": 1,
            "text": "",
            "bytes": -1,
            "records": 0,
            "truncated": False,
        },
        {
            "type": "recent-logs",
            "version": 1,
            "text": "",
            "bytes": 0,
            "records": 0,
            "truncated": 0,
        },
        {
            "type": "recent-logs",
            "version": 1,
            "text": "x",
            "bytes": 0,
            "records": 0,
            "truncated": False,
        },
        {
            "type": "recent-logs",
            "version": 1,
            "text": "",
            "bytes": 0,
            "records": "",
            "truncated": False,
        },
        {
            "type": "recent-logs",
            "version": 1,
            "text": "",
            "bytes": 0,
            "records": True,
            "truncated": False,
        },
        {
            "type": "recent-logs",
            "version": 1,
            "text": "",
            "bytes": 0,
            "records": -1,
            "truncated": False,
        },
    ],
)
async def test_logs_rejects_invalid_fields(mocker, event):
    process = protocol_process(mocker, [json.dumps(event)])
    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolError, match="invalid recent-log fields"):
        await client.logs()


@pytest.mark.asyncio
async def test_logs_cancellation_terminates_process(mocker):
    class BlockingReader:
        async def read(self):
            await asyncio.Event().wait()

    process = mocker.Mock()
    process.stdin = mocker.Mock()
    process.stdout = BlockingReader()
    process.stderr = BlockingReader()
    process.wait = mocker.AsyncMock()
    client = Client()
    client.connection = FakeConnectionContext(process)

    task = asyncio.create_task(client.logs())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    process.terminate.assert_called_once_with()
    process.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_execute_shell_decodes_streams_and_result(mocker):
    process = protocol_process(
        mocker,
        [
            '{"type":"shell-stream","stream":"stdout","encoding":"base64",'
            '"data":"b3V0"}\n',
            '{"type":"shell-stream","stream":"stderr","encoding":"base64",'
            '"data":"ZXJy"}\n',
            '{"type":"shell-result","version":1,"ok":true,"returncode":0}\n',
        ],
    )
    client = Client()
    client.connection = FakeConnectionContext(process)
    stdout = WriterWithoutFlush()
    stderr = WriterWithoutFlush()

    result = await client.execute_shell("command", stdout=stdout, stderr=stderr)

    assert process.command == SHELL_COMMAND
    request = json.loads(process.stdin.write.call_args.args[0])
    assert request == {"version": 1, "command": "command"}
    assert result == ShellResult(returncode=0)
    assert stdout.written == "out"
    assert stderr.written == "err"


@pytest.mark.asyncio
async def test_execute_shell_preserves_raw_bytes_for_bounded_writers(mocker):
    class ByteWriter:
        def __init__(self):
            self.data = bytearray()
            self.flushed = False

        def write_bytes(self, data):
            self.data.extend(data)

        def flush(self):
            self.flushed = True

    process = protocol_process(
        mocker,
        [
            '{"type":"shell-stream","stream":"stdout","encoding":"base64",'
            '"data":"/w=="}\n',
            '{"type":"shell-result","version":1,"ok":true,"returncode":0}\n',
        ],
    )
    client = Client()
    client.connection = FakeConnectionContext(process)
    stdout = ByteWriter()

    await client.execute_shell("command", stdout=stdout)

    assert stdout.data == b"\xff"
    assert stdout.flushed


@pytest.mark.asyncio
async def test_execute_shell_decodes_utf8_across_events_and_flushes_tail(mocker):
    process = protocol_process(
        mocker,
        [
            '{"type":"shell-stream","stream":"stdout","encoding":"base64",'
            '"data":"4g=="}\n',
            '{"type":"shell-stream","stream":"stdout","encoding":"base64",'
            '"data":"gqw="}\n',
            '{"type":"shell-stream","stream":"stderr","encoding":"base64",'
            '"data":"4g=="}\n',
            '{"type":"shell-result","version":1,"ok":true,"returncode":0}\n',
        ],
    )
    client = Client()
    client.connection = FakeConnectionContext(process)
    stdout = WriterWithoutFlush()
    stderr = WriterWithoutFlush()

    await client.execute_shell("command", stdout=stdout, stderr=stderr)

    assert stdout.written == "€"
    assert stderr.written == "�"


@pytest.mark.asyncio
async def test_jupyter_shell_uses_structured_protocol_and_merges_output(mocker):
    client = Client()
    execute_shell = mocker.patch.object(
        client,
        "execute_shell",
        new=mocker.AsyncMock(return_value=ShellResult(returncode=7)),
    )
    stdout = WriterWithoutFlush()

    await client.shell("exit 7", stdout=stdout, stderr=WriterWithoutFlush())

    execute_shell.assert_awaited_once_with(
        "exit 7",
        stdout=stdout,
        stderr=stdout,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["", "x" * 65537])
async def test_execute_shell_validates_command(command):
    client = Client()

    with pytest.raises(ValueError, match="1..65536"):
        await client.execute_shell(command)


@pytest.mark.asyncio
async def test_execute_shell_rejects_request_protocol_version(mocker):
    process = protocol_process(
        mocker,
        [],
        "Invalid shell request: request must use protocol version 1",
    )
    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolVersionError, match="not compatible"):
        await client.execute_shell("command")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lines", "diagnostic", "message"),
    [
        (["not-json\n"], "", "malformed shell"),
        (["null\n"], "", "non-object shell"),
        (['{"type":"other"}\n'], "", "unknown shell"),
        (
            [
                '{"type":"shell-result","version":1,"ok":true,"returncode":0}\n',
                '{"type":"shell-result","version":1,"ok":true,"returncode":0}\n',
            ],
            "",
            "after the final shell result",
        ),
        (
            [
                '{"type":"shell-result","version":1,"ok":true,"returncode":0}\n',
                '{"type":"shell-stream","stream":"stdout","encoding":"base64",'
                '"data":"bGF0ZQ=="}\n',
            ],
            "",
            "after the final shell result",
        ),
        ([], "", "no result"),
        ([], "diagnostic", "Remote stderr"),
        (
            ['{"type":"shell-result","version":1,"ok":true,"returncode":0}\n'],
            "diagnostic",
            "shell command failed",
        ),
    ],
)
async def test_execute_shell_rejects_invalid_protocol(
    mocker,
    lines,
    diagnostic,
    message,
):
    process = protocol_process(mocker, lines, diagnostic)
    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolError, match=message):
        await client.execute_shell("command")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "message"),
    [
        (
            {
                "type": "shell-result",
                "version": 1,
                "ok": "yes",
                "returncode": 0,
            },
            "result fields",
        ),
        (
            {
                "type": "shell-result",
                "version": 1,
                "ok": True,
                "returncode": True,
            },
            "result fields",
        ),
        (
            {
                "type": "shell-result",
                "version": 1,
                "ok": True,
                "returncode": 1,
            },
            "result fields",
        ),
        (
            {
                "type": "shell-result",
                "version": 1,
                "ok": True,
                "returncode": 0,
                "error": {},
            },
            "included an error",
        ),
        (
            {
                "type": "shell-result",
                "version": 1,
                "ok": False,
                "returncode": 1,
            },
            "no valid error",
        ),
        (
            {
                "type": "shell-result",
                "version": 1,
                "ok": False,
                "returncode": 1,
                "error": {"type": "other", "message": "bad"},
            },
            "no valid error",
        ),
        (
            {
                "type": "shell-result",
                "version": 1,
                "ok": False,
                "returncode": 1,
                "error": {"type": "ShellExitError", "message": 1},
            },
            "no valid error",
        ),
    ],
)
async def test_execute_shell_rejects_invalid_results(mocker, event, message):
    process = protocol_process(mocker, [json.dumps(event)])
    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolError, match=message):
        await client.execute_shell("command")


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [2, None])
async def test_execute_shell_rejects_result_protocol_version(mocker, version):
    event = {
        "type": "shell-result",
        "version": version,
        "ok": True,
        "returncode": 0,
    }
    process = protocol_process(mocker, [json.dumps(event)])
    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolVersionError, match=repr(version)):
        await client.execute_shell("command")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        {
            "type": "shell-stream",
            "stream": "other",
            "encoding": "base64",
            "data": "",
        },
        {
            "type": "shell-stream",
            "stream": "stdout",
            "encoding": "text",
            "data": "",
        },
        {
            "type": "shell-stream",
            "stream": "stdout",
            "encoding": "base64",
            "data": 1,
        },
        {
            "type": "shell-stream",
            "stream": "stdout",
            "encoding": "base64",
            "data": "***",
        },
    ],
)
async def test_execute_shell_rejects_invalid_stream_events(mocker, event):
    process = protocol_process(mocker, [json.dumps(event)])
    client = Client()
    client.connection = FakeConnectionContext(process)

    with pytest.raises(ProtocolError, match="shell stream|base64"):
        await client.execute_shell("command")


@pytest.mark.asyncio
async def test_execute_shell_cancellation_terminates_channel(mocker):
    class BlockingReader:
        async def readline(self):
            await asyncio.Event().wait()

    process = mocker.Mock()
    process.stdin = mocker.Mock()
    process.stdout = BlockingReader()
    process.stderr = BlockingReader()
    process.wait = mocker.AsyncMock()
    client = Client()
    client.connection = FakeConnectionContext(process)

    task = asyncio.create_task(client.execute_shell("command"))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    process.terminate.assert_called_once_with()
    process.wait.assert_awaited_once_with()
