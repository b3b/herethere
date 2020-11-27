from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path

import pytest

from herethere.there import commands
from herethere.there.commands import ContextObject


@pytest.fixture
def call_there_group(nested_event_loop, there):

    def _callable(args, code):
        commands.there_group(
            args, "test",
            standalone_mode=False,
            obj=ContextObject(client=there, code=code)
        )

    return _callable


def test_code_executed(call_there_group):
    out = StringIO()
    with redirect_stdout(out):
        call_there_group([], "print('hello')")
        assert out.getvalue() == "hello\n"


def test_shell_command_executed(call_there_group):
    out = StringIO()
    with redirect_stdout(out):
        call_there_group(["shell"], " echo hello")
        assert out.getvalue() == "hello\n"


def test_shell_command_executed(call_there_group):
    out = StringIO()
    with redirect_stdout(out):
        call_there_group(["shell"], " echo hello")
        assert out.getvalue() == "hello\n"


def test_file_uploaded(tmpdir, call_there_group):
    expected_path = Path(tmpdir) / 'hello_remote.txt'
    assert not os.path.exists(expected_path)

    call_there_group(["upload", "tests/hello.txt", "hello_remote.txt"], "")

    assert os.path.exists(expected_path)
    with open(expected_path) as f:
        assert f.read() == 'hello\n'


def test_file_uploaded(tmpdir, call_there_group):
    expected_path = Path(tmpdir) / 'hello_remote.txt'
    assert not os.path.exists(expected_path)

    call_there_group(["upload", "tests/hello.txt", "hello_remote.txt"], "")

    assert os.path.exists(expected_path)
    with open(expected_path) as f:
        assert f.read() == 'hello\n'


def test_multiple_files_uploaded_to_directory(tmpdir, call_there_group):
    assert not os.path.exists(Path(tmpdir) / 'hello.txt')
    assert not os.path.exists(Path(tmpdir) / 'hello/there.txt')

    call_there_group(["upload", "tests/hello.txt", "tests/hello", "."], "")

    assert os.path.exists(Path(tmpdir) / 'hello.txt')
    assert os.path.exists(Path(tmpdir) / 'hello/there.txt')

    for path in Path(tmpdir) / 'hello.txt', Path(tmpdir) / 'hello/there.txt':
        with open(path) as f:
            assert f.read() == 'hello\n'
