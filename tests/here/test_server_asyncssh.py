import asyncssh
import pytest

from herethere.here.server import MAX_COMMAND_LENGTH, SSHServerHere, handle_client


class TestServer(asyncssh.SSHServer):
    """SSH server used only for integration tests."""

    def begin_auth(self, username):
        return False


class RecordingChannel:
    """Records terminal output controls applied to the SSH process channel."""

    def __init__(self, events):
        self.events = events

    def set_echo(self, enabled):
        self.events.append(("set_echo", enabled))


class RecordingStdinChannel:
    """Records terminal input controls applied through process.stdin.channel."""

    def __init__(self, events):
        self.events = events

    def set_line_mode(self, enabled):
        self.events.append(("set_line_mode", enabled))


class RecordingInput:
    """Records when command input is read."""

    def __init__(self, channel, events):
        self.channel = channel
        self.events = events

    async def read(self, max_len=-1):
        self.events.append(("read", max_len))
        return "print('pong')"


class RecordingOutput:
    def __init__(self):
        self.written = ""

    def write(self, data):
        self.written += data

    async def drain(self):
        pass


class RecordingProcess:
    def __init__(self, command, terminal_type="xterm"):
        self.command = command
        self.terminal_type = terminal_type
        self.events = []
        self.channel = RecordingChannel(self.events)
        self.stdin = RecordingInput(
            RecordingStdinChannel(self.events),
            self.events,
        )
        self.stdout = RecordingOutput()
        self.stderr = RecordingOutput()
        self.exit_status = None

    def exit(self, status):
        self.exit_status = status

    def get_terminal_type(self):
        return self.terminal_type


@pytest.mark.asyncio
async def test_handle_client_configures_terminal_mode_before_reading_pty_input():
    """PTY input must be configured before herethere reads submitted code."""

    process = RecordingProcess(command="code")

    await handle_client(process, namespace={})

    assert process.events == [
        ("set_echo", False),
        ("set_line_mode", True),
        ("read", MAX_COMMAND_LENGTH),
    ]
    assert process.stdout.written == "pong\n"
    assert process.stderr.written == ""
    assert process.exit_status == 0


@pytest.mark.asyncio
async def test_handle_client_accepts_missing_namespace_and_non_pty_input():
    process = RecordingProcess(command="code", terminal_type=None)

    await handle_client(process, namespace=None)

    assert process.events == [("read", MAX_COMMAND_LENGTH)]
    assert process.stdout.written == "pong\n"
    assert process.exit_status == 0


@pytest.mark.asyncio
async def test_handle_client_unknown_command_exits_cleanly():
    process = RecordingProcess(command="unknown-command")

    await handle_client(process, namespace={})

    assert process.stderr.written == "Unknown command"
    assert process.exit_status == 0


def test_connection_lost_with_exception_logged(mocker):
    server = SSHServerHere("user", "password", executor=mocker.Mock())
    logger = mocker.patch("herethere.here.server.logger")

    server.connection_lost(RuntimeError("boom"))

    logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_ping_command_works_over_real_asyncssh_session(unused_tcp_port):
    """Run the ping command through a real AsyncSSH client/server session."""

    async def process_factory(process):
        await handle_client(process, namespace={})

    server = await asyncssh.create_server(
        TestServer,
        "127.0.0.1",
        unused_tcp_port,
        server_host_keys=[asyncssh.generate_private_key("ssh-rsa")],
        process_factory=process_factory,
        line_editor=True,
    )

    try:
        async with asyncssh.connect(
            "127.0.0.1",
            port=unused_tcp_port,
            username="test",
            known_hosts=None,
        ) as conn:
            result = await conn.run(
                "ping",
                input="",
                term_type="xterm",
                check=True,
            )

        assert result.stdout == "pong"
        assert result.stderr == ""
        assert result.exit_status == 0

    finally:
        server.close()
        await server.wait_closed()
