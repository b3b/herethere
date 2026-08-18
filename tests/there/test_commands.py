import errno
import logging
import os
import threading
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import herethere.there.commands.log  # noqa: F401
from herethere.everywhere import runcode
from herethere.there.client import ExecutionResult, RemoteError
from herethere.there.commands.core import (
    ContextObject,
    EmptyCode,
    NeedDisplay,
    raw_remainder_after_command,
    there_code_shortcut,
    there_group,
)
from herethere.there.commands.log import LOG_COMMAND_TEMPLATE
from herethere.there.history import RecentThereHistory


class ForegroundClientStub:
    """Foreground client stub which records work done by a separate instance."""

    def __init__(self):
        self.calls = []
        self.copied = None

    async def copy(self):
        self.copied = SeparateClientInstanceStub(self.calls)
        return self.copied


class SeparateClientInstanceStub:
    """Separate client instance used by background commands."""

    def __init__(self, calls):
        self.calls = calls

    async def runcode_background(self, code, stdout=None, stderr=None):
        self.calls.append(("runcode_background", code, stdout, stderr))

    async def shell(self, code, stdout=None, stderr=None):
        self.calls.append(("shell", code, stdout, stderr))

    async def disconnect(self):
        self.calls.append(("disconnect",))


class FailingSeparateClientInstanceStub(SeparateClientInstanceStub):
    """Separate client instance that fails while running a command."""

    async def shell(self, code, stdout=None, stderr=None):
        self.calls.append(("shell", code, stdout, stderr))
        raise RuntimeError("shell failed")


class FailingForegroundClientStub(ForegroundClientStub):
    """Foreground client stub that returns a failing separate instance."""

    async def copy(self):
        self.copied = FailingSeparateClientInstanceStub(self.calls)
        return self.copied


class GetClientStub:
    async def get(self, expression):
        return eval(expression)  # pylint: disable=eval-used


class RunClientStub:
    async def runcode(self, code, stdout=None, stderr=None):
        return None


class WorkerClientStub:
    def __init__(self, result=None, stdout_text="", stderr_text=""):
        self.calls = []
        self.result = result
        self.stdout_text = stdout_text
        self.stderr_text = stderr_text

    async def execute_worker(self, code, stdout=None, stderr=None):
        self.calls.append((code, stdout, stderr))
        if stdout is not None:
            stdout.write(self.stdout_text)
        if stderr is not None:
            stderr.write(self.stderr_text)
        return self.result


class ClosingSSHStreamStub:
    """SSH stdout-like stream which starts raising once the channel is closed."""

    def __init__(self, error_factory):
        self.closed = False
        self.error_factory = error_factory
        self.writes = []

    def write(self, data):
        if self.closed:
            raise self.error_factory()
        self.writes.append(data)

    def close(self):
        self.closed = True


def test_code_executed(call_there_group):
    out = StringIO()
    with redirect_stdout(out):
        call_there_group([], "print('hello')")
        assert out.getvalue() == "hello\n"


def test_python_cell_records_history():
    history = RecentThereHistory()

    there_group(
        [],
        "test",
        standalone_mode=False,
        obj=ContextObject(
            client=RunClientStub(),
            code="print('hello')",
            raw_line="-b",
            history=history,
        ),
    )

    latest = history.latest()
    assert latest is not None
    assert latest.line == "-b"
    assert latest.cell == "print('hello')"


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: BrokenPipeError("Channel not open for sending"),
        lambda: OSError(errno.EPIPE, "Channel not open for sending"),
    ],
)
def test_log_command_closed_stdout_removes_handler_and_exits(error_factory):
    stream = ClosingSSHStreamStub(error_factory)
    ssh_server_closed = threading.Event()
    listener_done = threading.Event()
    thread_errors = []
    root_logger = logging.getLogger()

    def run_log_command():
        try:
            runcode(
                LOG_COMMAND_TEMPLATE,
                stdout=stream,
                stderr=StringIO(),
                namespace={"ssh_server_closed": ssh_server_closed},
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            thread_errors.append(exc)
        finally:
            listener_done.set()

    thread = threading.Thread(target=run_log_command, daemon=True)
    thread.start()

    try:
        for _ in range(50):
            handlers = [
                handler
                for handler in root_logger.handlers
                if getattr(handler, "stream", None) is stream
            ]
            if handlers:
                break
            listener_done.wait(0.02)

        assert handlers

        stream.close()
        root_logger.warning("log record after log client disconnect")

        assert listener_done.wait(1)
        assert not thread_errors
        assert all(
            getattr(handler, "stream", None) is not stream
            for handler in root_logger.handlers
        )
    finally:
        ssh_server_closed.set()
        listener_done.wait(1)
        for handler in list(root_logger.handlers):
            if getattr(handler, "stream", None) is stream:
                root_logger.removeHandler(handler)
                handler.close()


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


def test_worker_python_code_uses_synchronous_worker_operation():
    client = WorkerClientStub(ExecutionResult())
    ctx = ContextObject(client=client, code="print('hello')")

    there_group(
        ["--worker"],
        "test",
        standalone_mode=False,
        obj=ctx,
    )

    assert ctx.worker is True
    assert client.calls == [("# %%there ... \nprint('hello')", None, None)]


def test_worker_python_code_replays_buffered_streams():
    stdout = StringIO()
    stderr = StringIO()
    client = WorkerClientStub(
        ExecutionResult(),
        stdout_text="out\n",
        stderr_text="err\n",
    )

    there_group(
        ["--worker"],
        "test",
        standalone_mode=False,
        obj=ContextObject(
            client=client,
            code="print('hello')",
            stdout=stdout,
            stderr=stderr,
        ),
    )

    assert stdout.getvalue() == "out\n"
    assert stderr.getvalue() == "err\n"


def test_worker_python_code_propagates_structured_error():
    client = WorkerClientStub(
        ExecutionResult(
            error=RemoteError(
                remote_type="RuntimeError",
                message="boom",
                traceback="Traceback\nRuntimeError: boom",
            )
        ),
        stderr_text="Traceback\nRuntimeError: boom\n",
    )
    stderr = StringIO()

    with pytest.raises(click.ClickException, match="RuntimeError: boom") as exc:
        there_group(
            ["--worker"],
            "test",
            standalone_mode=False,
            obj=ContextObject(
                client=client,
                code="raise RuntimeError('boom')",
                stderr=stderr,
            ),
        )

    assert "Traceback" not in exc.value.message
    assert stderr.getvalue() == "Traceback\nRuntimeError: boom\n"


@pytest.mark.parametrize(
    "args, message",
    (
        (
            ["--worker", "--background"],
            "--worker and --background cannot be used together.",
        ),
        (["--worker", "shell"], "--worker can only be used for Python code"),
        (["--worker", "get", "1 + 1"], "--worker can only be used for Python code"),
    ),
)
def test_worker_rejects_invalid_magic_command_paths(args, message):
    with pytest.raises(click.UsageError, match=message):
        there_group(
            args,
            "test",
            standalone_mode=False,
            obj=ContextObject(client=RunClientStub(), code="print('hello')"),
        )


@pytest.mark.asyncio
async def test_background_python_code_uses_separate_client_instance():
    """Background Python execution must run through a separate client instance."""
    client = ForegroundClientStub()
    stdout = StringIO()
    stderr = StringIO()
    ctx = ContextObject(
        client=client,
        code="print('hello')",
        stdout=stdout,
        stderr=stderr,
    )
    ctx.background = True

    future = ctx.runcode()
    future.result(timeout=1)

    assert client.calls == [
        ("runcode_background", "# %%there ... \nprint('hello')", stdout, stderr),
        ("disconnect",),
    ]


@pytest.mark.asyncio
async def test_background_shell_uses_separate_client_instance():
    """Background shell execution must run through a separate client instance."""
    client = ForegroundClientStub()
    stdout = StringIO()
    stderr = StringIO()
    ctx = ContextObject(client=client, code="echo hello", stdout=stdout, stderr=stderr)
    ctx.background = True

    future = ctx.shell()
    future.result(timeout=1)

    assert client.calls == [
        ("shell", "echo hello", stdout, stderr),
        ("disconnect",),
    ]


@pytest.mark.asyncio
async def test_background_command_disconnects_separate_client_after_failure():
    """Background command cleanup must run if the command fails."""
    client = FailingForegroundClientStub()
    ctx = ContextObject(client=client, code="echo hello", stdout=StringIO())
    ctx.background = True

    future = ctx.shell()
    with pytest.raises(RuntimeError, match="shell failed"):
        future.result(timeout=1)

    assert client.calls == [
        ("shell", "echo hello", ctx.stdout, None),
        ("disconnect",),
    ]


@pytest.mark.asyncio
async def test_background_command_with_display_sets_context_background():
    """The command group enables background mode once display streams exist."""
    stdout = StringIO()
    stderr = StringIO()
    ctx = ContextObject(
        client=ForegroundClientStub(),
        code="print('hello')",
        stdout=stdout,
        stderr=stderr,
    )

    future = there_group(
        ["--background"],
        "test",
        standalone_mode=False,
        obj=ctx,
    )
    future.result(timeout=1)

    assert ctx.background is True


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


def test_get_command_returns_value(call_there_group):
    result = call_there_group(["get", "1", "+", "1"], "")

    assert result == 2


def test_get_command_uses_cell_when_expression_not_in_line(call_there_group):
    assert call_there_group(["get"], "1 + 2") == 3


def test_get_command_rejects_empty_expression(call_there_group):
    with pytest.raises(EmptyCode, match="Expression to evaluate"):
        call_there_group(["get"], "")


def test_get_command_rejects_background():
    stdout = StringIO()
    stderr = StringIO()
    ctx = ContextObject(
        client=GetClientStub(),
        code="1 + 1",
        stdout=stdout,
        stderr=stderr,
    )

    with pytest.raises(click.ClickException, match="get cannot be used"):
        there_group(
            ["--background", "get"],
            "test",
            standalone_mode=False,
            obj=ctx,
        )


def test_get_command_sets_expression_from_line():
    ctx = ContextObject(client=GetClientStub(), code="")

    result = there_group(
        ["get", "1", "+", "2"],
        "test",
        standalone_mode=False,
        obj=ctx,
    )

    assert result == 3
    assert ctx.code == "1 + 2"


def test_get_command_prefers_raw_line_expression():
    ctx = ContextObject(
        client=GetClientStub(),
        code="",
        raw_line='--delay 0 get "a  b"',
    )

    there_group(
        ["--delay", "0", "get", "a  b"],
        "test",
        standalone_mode=False,
        obj=ctx,
    )

    assert ctx.code == '"a  b"'


def test_raw_remainder_after_command_returns_empty_without_raw_line():
    ctx = click.Context(
        click.Command("get"),
        info_name="get",
        obj=ContextObject(GetClientStub(), ""),
    )

    assert raw_remainder_after_command(ctx) == ""


def test_raw_remainder_after_command_returns_empty_when_command_missing():
    ctx = click.Context(
        click.Command("get"),
        info_name="get",
        obj=ContextObject(GetClientStub(), "", raw_line="shell echo hello"),
    )

    assert raw_remainder_after_command(ctx) == ""


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


def test_file_uploaded_to_default_directory(tmpdir, call_there_group):
    expected_path = Path(tmpdir) / "hello.txt"
    assert not os.path.exists(expected_path)

    call_there_group(["upload", "tests/hello.txt"], "")

    assert expected_path.read_text() == "hello\n"


def test_multiple_files_uploaded_to_directory(tmpdir, call_there_group):
    assert not os.path.exists(Path(tmpdir) / "hello.txt")
    assert not os.path.exists(Path(tmpdir) / "hello/there.txt")

    call_there_group(["upload", "tests/hello.txt", "tests/hello", "."], "")

    assert os.path.exists(Path(tmpdir) / "hello.txt")
    assert os.path.exists(Path(tmpdir) / "hello/there.txt")

    for path in Path(tmpdir) / "hello.txt", Path(tmpdir) / "hello/there.txt":
        with open(path) as f:
            assert f.read() == "hello\n"


def test_file_downloaded(tmpdir, call_there_group):
    remote_path = Path(tmpdir) / "hello_remote.txt"
    local_path = Path(tmpdir) / "hello_local.txt"
    remote_path.write_text("hello remote\n")
    assert not os.path.exists(local_path)

    call_there_group(["download", "hello_remote.txt", str(local_path)], "")

    assert local_path.read_text() == "hello remote\n"


def test_file_downloaded_to_default_directory(tmpdir, monkeypatch, call_there_group):
    remote_path = Path(tmpdir) / "hello_remote.txt"
    local_dir = Path(tmpdir) / "local"
    remote_path.write_text("hello remote\n")
    local_dir.mkdir()
    monkeypatch.chdir(local_dir)

    call_there_group(["download", "hello_remote.txt"], "")

    assert (local_dir / "hello_remote.txt").read_text() == "hello remote\n"


@pytest.mark.parametrize(
    "command, expected_usage, expected_help",
    (
        (
            "upload",
            "Usage: there upload [OPTIONS] LOCAL_PATH... [REMOTE_PATH]",
            "With one path, upload to the current remote SFTP directory.",
        ),
        (
            "download",
            "Usage: there download [OPTIONS] REMOTE_PATH... [LOCAL_PATH]",
            "With one path, download to the current local directory.",
        ),
    ),
)
def test_transfer_command_help(tmpdir, command, expected_usage, expected_help):
    result = CliRunner().invoke(
        there_group,
        [command, "--help"],
        obj=ContextObject(client=GetClientStub(), code=""),
    )

    assert result.exit_code == 0
    assert expected_usage in result.output
    assert expected_help in result.output


def test_multiple_files_downloaded_to_directory(tmpdir, call_there_group):
    remote_file = Path(tmpdir) / "hello_remote.txt"
    remote_dir = Path(tmpdir) / "remote_dir"
    local_dir = Path(tmpdir) / "downloads"
    remote_file.write_text("hello remote\n")
    remote_dir.mkdir()
    (remote_dir / "there.txt").write_text("hello there\n")
    assert not os.path.exists(local_dir)

    call_there_group(
        ["download", "hello_remote.txt", "remote_dir", str(local_dir)],
        "",
    )

    assert (local_dir / "hello_remote.txt").read_text() == "hello remote\n"
    assert (local_dir / "remote_dir/there.txt").read_text() == "hello there\n"


def test_there_code_shortcut(call_there_group):

    @there_code_shortcut
    @click.option("-s", "--some-option")
    @click.argument("somearg")
    def _test_shortcut(code, somearg, some_option):
        assert code is ...
        assert some_option is None
        assert somearg == "arg value test"
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
