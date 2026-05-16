import asyncio
import contextlib
import os
from pathlib import Path

import asyncssh
import pytest

from herethere.here.server import RunningServer, SSHServerHere, start_server


@pytest.mark.asyncio
async def test_server_is_serving(server_instance):
    assert server_instance.is_serving()
    assert server_instance


@pytest.mark.asyncio
async def test_client_connected(server_instance, connection_config):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None):
        pass


@pytest.mark.asyncio
async def test_server_stop_disconnects_sleeping_client(
    server_config,
    connection_config,
):
    """
    A connected SSH client must not be able to keep the debug server alive forever.
    """
    server_instance = await start_server(server_config)
    client_connected = asyncio.Event()

    async def keep_client_connected():
        """Open an SSH connection and wait until the server closes it."""
        async with asyncssh.connect(
            **connection_config.asdict,
            known_hosts=None,
        ) as conn:
            client_connected.set()
            await conn.wait_closed()

    client_task = asyncio.create_task(
        keep_client_connected(),
        name="sleeping debug client",
    )
    try:
        await asyncio.wait_for(client_connected.wait(), timeout=1)

        await asyncio.wait_for(server_instance.stop(), timeout=1)

        assert not server_instance.is_serving()

        # The server should have disconnected the active client.
        await asyncio.wait_for(client_task, timeout=1)
    finally:
        if not client_task.done():
            client_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client_task

        with contextlib.suppress(Exception):
            await asyncio.wait_for(server_instance.stop(), timeout=1)


class FakeAsyncioServer:
    """Minimal fake for the AsyncSSH server/acceptor."""

    def __init__(self):
        self.closed = False
        self.wait_closed_called = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.wait_closed_called = True

    def is_serving(self):
        return not self.closed


class StuckAsyncioServer(FakeAsyncioServer):
    async def wait_closed(self):
        self.wait_closed_called = True
        await asyncio.Future()


class StuckConnection:
    """Connection where graceful close never completes."""

    def __init__(self):
        self.close_called = False
        self.abort_called = False

    def close(self):
        self.close_called = True

    def abort(self):
        self.abort_called = True

    async def wait_closed(self):
        await asyncio.Future()  # never completes


class AbortCompletesConnection:
    """Connection where abort allows wait_closed() to finish."""

    def __init__(self):
        self.close_called = False
        self.abort_called = False
        self._closed = None

    def close(self):
        self.close_called = True

    def abort(self):
        self.abort_called = True
        self._closed.set_result(None)

    async def wait_closed(self):
        self._closed = asyncio.Future()
        await self._closed


class FakeExecutor:
    def __init__(self):
        self.shutdown_called = False
        self.shutdown_wait = None

    def shutdown(self, wait=True):
        self.shutdown_called = True
        self.shutdown_wait = wait


def test_connection_made_tracks_connection_and_unknown_peer(mocker):
    connections = set()
    conn = mocker.Mock()
    conn.get_extra_info.return_value = None
    logger = mocker.patch("herethere.here.server.logger")
    server = SSHServerHere(
        "user",
        "password",
        executor=mocker.Mock(),
        connections=connections,
    )

    server.connection_made(conn)

    assert server.conn is conn
    assert connections == {conn}
    logger.info.assert_called_once_with("SSH connection received from %s.", "unknown")


def test_connection_made_accepts_missing_connection_tracker(mocker):
    conn = mocker.Mock()
    conn.get_extra_info.return_value = ("127.0.0.1", 12345)
    logger = mocker.patch("herethere.here.server.logger")
    server = SSHServerHere("user", "password", executor=mocker.Mock())

    server.connection_made(conn)

    assert server.conn is conn
    logger.info.assert_called_once_with("SSH connection received from %s.", "127.0.0.1")


def test_connection_lost_removes_tracked_connection(mocker):
    conn = mocker.Mock()
    server = SSHServerHere(
        "user",
        "password",
        executor=mocker.Mock(),
        connections={conn},
    )
    server.conn = conn

    server.connection_lost(None)

    assert server.conn is None
    assert server.connections == set()


@pytest.mark.asyncio
async def test_stop_aborts_stuck_connection_and_returns():
    """
    RunningServer.stop() must not hang forever if a client connection
    does not finish graceful close.

    This simulates an AsyncSSH connection where:

        conn.close()
        await conn.wait_closed()

    never completes.

    Expected behavior:
    - server listener is closed
    - graceful conn.close() is attempted
    - stuck connection is aborted
    - executor is shut down
    - stop() returns promptly
    """
    fake_server = FakeAsyncioServer()
    fake_executor = FakeExecutor()
    stuck_conn = StuckConnection()
    namespace = {}

    server = RunningServer(
        server=fake_server,
        namespace=namespace,
        executor=fake_executor,
        connections={stuck_conn},
    )

    await asyncio.wait_for(server.stop(timeout=0.01), timeout=1)

    assert namespace["ssh_server_closed"].is_set()
    assert fake_server.closed
    assert fake_server.wait_closed_called
    assert stuck_conn.close_called
    assert stuck_conn.abort_called
    assert fake_executor.shutdown_called
    assert fake_executor.shutdown_wait is False


@pytest.mark.asyncio
async def test_stop_handles_connection_closed_after_abort():
    fake_server = FakeAsyncioServer()
    fake_executor = FakeExecutor()
    conn = AbortCompletesConnection()
    server = RunningServer(
        server=fake_server,
        namespace={},
        executor=fake_executor,
        connections={conn},
    )

    await asyncio.wait_for(server.stop(timeout=0.01), timeout=1)

    assert conn.close_called
    assert conn.abort_called
    assert fake_executor.shutdown_called


@pytest.mark.asyncio
async def test_stop_logs_server_wait_closed_timeout(mocker):
    fake_server = StuckAsyncioServer()
    fake_executor = FakeExecutor()
    logger = mocker.patch("herethere.here.server.logger")
    server = RunningServer(
        server=fake_server,
        namespace={},
        executor=fake_executor,
        connections=set(),
    )

    await asyncio.wait_for(server.stop(timeout=0.01), timeout=1)

    assert fake_server.wait_closed_called
    logger.debug.assert_called_with("SSH server wait_closed timed out.")


@pytest.mark.asyncio
async def test_wait_for_connection_tasks_accepts_empty_task_set():
    server = RunningServer(
        server=FakeAsyncioServer(),
        namespace={},
        executor=FakeExecutor(),
        connections=set(),
    )

    pending = await server._wait_for_connection_tasks(set(), "close", timeout=0.01)

    assert pending == set()


@pytest.mark.asyncio
async def test_log_wait_closed_results_handles_cancelled_and_failed_tasks(mocker):
    logger = mocker.patch("herethere.here.server.logger")

    async def cancelled():
        raise asyncio.CancelledError

    async def failed():
        raise RuntimeError("boom")

    cancelled_task = asyncio.create_task(cancelled())
    failed_task = asyncio.create_task(failed())
    await asyncio.wait({cancelled_task, failed_task})

    RunningServer._log_wait_closed_results(
        {cancelled_task, failed_task},
        action="close",
    )

    logger.debug.assert_any_call("SSH connection %s wait was cancelled.", "close")
    logger.debug.assert_any_call(
        "SSH connection %s finished with error: %r",
        "close",
        failed_task.exception(),
    )


@pytest.mark.asyncio
async def test_pong_returned(server_instance, connection_config):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None) as conn:
        result = await conn.run("ping", check=True)
        assert result.stdout == "pong"


@pytest.mark.asyncio
async def test_line_executed(server_instance, connection_config):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None) as conn:
        result = await conn.run("code", check=True, input="print('hello there')")
        assert result.stdout == "hello there\n"


@pytest.mark.asyncio
async def test_backgroud_line_executed(server_instance, connection_config):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None) as conn:
        result = await conn.run("background", check=True, input="print('hello there')")
        assert result.stdout == "hello there\n"


@pytest.mark.asyncio
async def test_shell_line_executed(server_instance, connection_config):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None) as conn:
        result = await conn.run("shell", check=True, input="echo hello there")
        assert result.stdout == "hello there\n"


@pytest.mark.asyncio
async def test_global_variable_available(server_instance, connection_config):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None) as conn:
        result = await conn.run(
            "code", check=True, input="print(test_variable_in_namespace)"
        )
        assert not result.stderr
        assert result.stdout == "OK\n"


@pytest.mark.asyncio
async def test_namespace_variable_updated(
    server_instance, connection_config, tmp_environ
):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None) as conn:
        result = await conn.run(
            "code", check=True, input="print(test_global_variable_updated_var)"
        )
        assert not result.stdout
        assert "NameError:" in result.stderr

        result = await conn.run(
            "code", check=True, input="test_global_variable_updated_var = 1"
        )
        result = await conn.run(
            "code", check=True, input="print(test_global_variable_updated_var)"
        )
        assert result.stdout == "1\n"
        assert result.stderr == ""


@pytest.mark.asyncio
async def test_sftp_file_uploaded(
    server_config, server_instance, client_instance, tmpdir
):
    expected_path = os.path.join(tmpdir, "hello_remote.txt")
    assert not os.path.exists(expected_path)

    async with client_instance as conn:
        async with conn.start_sftp_client() as sftp:
            await sftp.put("tests/hello.txt", "hello_remote.txt")

    assert os.path.exists(expected_path)


@pytest.mark.asyncio
async def test_new_key_generated_if_not_exist(tmpdir, server_config):
    path = Path(tmpdir) / "test_key_do_not_exist.rsa"
    assert not os.path.exists(path)
    server_config.key_path = path

    server_instance = await start_server(server_config)

    try:
        assert os.path.exists(path)
        assert server_instance.is_serving()
    finally:
        await server_instance.stop()


class CustomSSHServerHere(SSHServerHere):
    test_events = []

    def connection_made(self, *args, **kwargs):
        CustomSSHServerHere.test_events.append("connection_made")


@pytest.mark.asyncio
async def test_custom_server_class_used(server_config, connection_config):
    assert not CustomSSHServerHere.test_events

    await start_server(server_config, server_factory=CustomSSHServerHere)
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None):
        pass

    assert CustomSSHServerHere.test_events == ["connection_made"]


@pytest.mark.asyncio
async def test_custom_server_class_bad_type(server_config):
    with pytest.raises(TypeError):
        await start_server(server_config, server_factory=object)
