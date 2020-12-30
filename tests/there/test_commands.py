from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path

import click
import pytest

from herethere.there.commands.core import (
    EmptyCode,
    NeedDisplay,
    there_code_shortcut,
)
from herethere.there.commands.log import log


def test_code_executed(call_there_group):
    out = StringIO()
    with redirect_stdout(out):
        call_there_group([], "print('hello')")
        assert out.getvalue() == "hello\n"


def test_exception_on_empty_code(call_there_group):
    with pytest.raises(EmptyCode):
        call_there_group([], "")


def test_background_display_required(call_there_group):
    with pytest.raises(NeedDisplay) as exc:
        call_there_group(["--background"], "print('hello')")
    assert exc.value.maxlen == 24


def test_background_display_max_lines_applied(call_there_group):
    with pytest.raises(NeedDisplay) as exc:
        call_there_group(["-bl", "100"], "print('hello')")
    assert exc.value.maxlen == 100


def test_execution_delayed(capfd, mocker, call_there_group):
    sleep = mocker.patch("time.sleep")
    call_there_group(["--delay", "100.5"], "print('hello')")
    sleep.assert_called_once_with(100.5)
    assert capfd.readouterr().out == "hello\n"


def test_shell_command_executed(call_there_group):
    out = StringIO()
    with redirect_stdout(out):
        call_there_group(["shell"], " echo hello")
        assert out.getvalue() == "hello\n"


def test_exception_on_empty_shell_code(call_there_group):
    with pytest.raises(EmptyCode):
        call_there_group(["shell"], "")


def test_file_uploaded(tmpdir, call_there_group):
    expected_path = Path(tmpdir) / "hello_remote.txt"
    assert not os.path.exists(expected_path)

    call_there_group(["upload", "tests/hello.txt", "hello_remote.txt"], "")

    assert os.path.exists(expected_path)
    with open(expected_path) as f:
        assert f.read() == "hello\n"


def test_file_uploaded(tmpdir, call_there_group):
    expected_path = Path(tmpdir) / "hello_remote.txt"
    assert not os.path.exists(expected_path)

    call_there_group(["upload", "tests/hello.txt", "hello_remote.txt"], "")

    assert os.path.exists(expected_path)
    with open(expected_path) as f:
        assert f.read() == "hello\n"


def test_multiple_files_uploaded_to_directory(tmpdir, call_there_group):
    assert not os.path.exists(Path(tmpdir) / "hello.txt")
    assert not os.path.exists(Path(tmpdir) / "hello/there.txt")

    call_there_group(["upload", "tests/hello.txt", "tests/hello", "."], "")

    assert os.path.exists(Path(tmpdir) / "hello.txt")
    assert os.path.exists(Path(tmpdir) / "hello/there.txt")

    for path in Path(tmpdir) / "hello.txt", Path(tmpdir) / "hello/there.txt":
        with open(path) as f:
            assert f.read() == "hello\n"


def test_there_code_shortcut(call_there_group):

    @there_code_shortcut
    @click.option("-s", "--some-option")
    @click.argument("somearg")
    def _test_shortcut(code, somearg, some_option):
        assert code is ...
        assert some_option is None
        assert somearg == 'arg value test'
        return "print('hello from shortcut')"

    out = StringIO()
    with redirect_stdout(out):
        call_there_group(["_test_shortcut", "arg value test"], ...)
        assert out.getvalue() == "hello from shortcut\n"


def test_log_command_ended(capfd, server_instance, call_there_group):
    server_instance.namespace["ssh_server_closed"].set()

    call_there_group(["log"], "")

    captured = capfd.readouterr()
    assert not captured.out
    assert not captured.err
