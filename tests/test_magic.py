import asyncio
from contextlib import redirect_stdout
from io import StringIO

import pytest
import pytest_asyncio
from IPython.core.interactiveshell import InteractiveShell

from herethere.magic import MagicThere
from herethere.there.commands import NeedDisplay
from herethere.there.output import LimitedOutput


@pytest_asyncio.fixture
async def connected_there(server_instance, connection_config):
    magic = MagicThere(shell=None)
    await magic.client.connect(connection_config)
    assert magic.client.connection
    yield magic
    await magic.client.disconnect()


@pytest.mark.asyncio
async def test_connected(server_instance, connection_config, tmp_environ):
    tmp_environ["THERE_PORT"] = str(connection_config.port)
    magic = MagicThere(shell=None)
    await magic.client.connect(connection_config)
    assert magic.client.connection
    await magic.client.disconnect()


def test_connect_magic_connects_client(mocker, tmp_environ):
    tmp_environ.update(
        {
            "THERE_HOST": "localhost",
            "THERE_PORT": "9022",
            "THERE_USERNAME": "here",
            "THERE_PASSWORD": "there",
        }
    )
    magic = MagicThere(shell=None)
    connect = mocker.patch.object(
        magic.client,
        "connect",
        new=mocker.Mock(return_value="connect-result"),
    )
    run = mocker.patch("herethere.there.magic.run_sync")

    magic.connect("")

    run.assert_called_once_with("connect-result")
    connect.assert_called_once()


@pytest.mark.asyncio
async def test_connect_there_can_reload_extension_without_blocking_server_loop(
    server_instance, connection_config, tmp_environ, tmp_path
):
    tmp_environ.update(
        {
            "IPYTHONDIR": str(tmp_path),
            "THERE_HOST": connection_config.host,
            "THERE_PORT": str(connection_config.port),
            "THERE_USERNAME": connection_config.username,
            "THERE_PASSWORD": connection_config.password,
        }
    )
    shell = InteractiveShell()

    # The in-process SSH server runs on this pytest event loop. Run synchronous
    # IPython magic calls in a worker thread so the server can accept/respond.
    await asyncio.to_thread(shell.run_line_magic, "load_ext", "herethere.magic")
    await asyncio.to_thread(shell.run_line_magic, "connect-there", "")

    await asyncio.to_thread(shell.run_line_magic, "reload_ext", "herethere.magic")
    await asyncio.to_thread(shell.run_line_magic, "connect-there", "")


def test_code_executed(call_there_group):
    out = StringIO()
    with redirect_stdout(out):
        call_there_group([], "print('hello')")
    assert out.getvalue() == "hello\n"


@pytest.mark.parametrize(
    "line, code, expected_args, expected_code",
    (
        ["", "print(1)", [], "print(1)"],
        ["shell", "echo 1", ["shell"], "echo 1"],
        ["upload tests/hello.txt dst", "", ["upload", "tests/hello.txt", "dst"], ""],
        ["upload src1 src2 dst", "", ["upload", "src1", "src2", "dst"], ""],
    ),
)
def test_there_command_called(
    mocker, connected_there, line, code, expected_args, expected_code
):
    command = mocker.patch("herethere.there.magic.there_group")
    connected_there.there(line, code)
    command.assert_called_once()
    assert command.call_args[0][0] == expected_args
    assert command.call_args[1]["obj"].code == expected_code


def test_local_command_intercepts_before_there_group(mocker):
    magic = MagicThere(shell=None)
    local_handler = mocker.patch(
        "herethere.there.magic.maybe_handle_local_there_command",
        return_value=True,
    )
    command = mocker.patch("herethere.there.magic.there_group")

    magic.there("ai", "debug remote")

    local_handler.assert_called_once_with(
        "ai",
        "debug remote",
        shell=None,
        history=magic.recent_there_history,
    )
    command.assert_not_called()


def test_error_line_number(capfd, call_there_group):
    call_there_group([], "print 1")
    captured = capfd.readouterr()
    assert 'File "<string>", line 2\n' in captured.err
    assert "SyntaxError:" in captured.err


@pytest.mark.asyncio
async def test_limited_output_created(mocker, connected_there):
    command = mocker.patch(
        "herethere.there.magic.there_group", side_effect=NeedDisplay(maxlen=1)
    )

    with pytest.raises(NeedDisplay):
        connected_there.there("-b", "print('hello')")

    assert command.call_count == 2
    ctx = command.call_args[1]["obj"]
    assert isinstance(ctx.stdout, LimitedOutput)
