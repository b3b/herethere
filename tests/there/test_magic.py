from contextlib import redirect_stdout
from io import StringIO

import pytest

from herethere.magic import MagicThere
from herethere.there.commands import NeedDisplay
from herethere.there.output import LimitedOutput


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
        connected_there.there("", "print('hello')")
    assert out.getvalue() == "hello\n"


@pytest.mark.parametrize(
    "line, code, expected_args, expected_code",
    (
        ["", "print(1)", [], "print(1)"],
        ["shell", "echo 1", ["shell"], "echo 1"],
        ["upload tests/hello.txt dst", "", ["upload", "tests/hello.txt", "dst"], ""],
        ["upload tests/'hello there.txt' dst", "", ["upload", "tests/hello there.txt", "dst"], ""],
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


def test_error_line_number(capfd, connected_there):
    connected_there.there("", "print 1")
    captured = capfd.readouterr()
    assert captured.err.strip().startswith('File "<string>", line 2\n')


@pytest.mark.asyncio
async def test_limited_output_created(mocker, connected_there):
    command = mocker.patch(
        "herethere.there.magic.there_group",
        side_effect=NeedDisplay(maxlen=1)
    )

    with pytest.raises(NeedDisplay):
        connected_there.there("-b", "print('hello')")

    assert command.call_count == 2
    ctx = command.call_args[1]["obj"]
    assert isinstance(ctx.stdout, LimitedOutput)
