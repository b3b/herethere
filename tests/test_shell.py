import asyncio
import json
import os
import signal
from io import StringIO

import pytest

from herethere.everywhere.shell import ShellResult
from herethere.here.shell import (
    StructuredShellStream,
    _pump_shell_stream,
    _signal_shell_process,
    _stop_shell_process,
    decode_shell_request,
    run_shell,
)


class ByteSink:
    def __init__(self, error=None):
        self.data = bytearray()
        self.finished = False
        self.error = error

    def write(self, data):
        if self.error is not None:
            raise self.error
        self.data.extend(data)

    def finish(self):
        self.finished = True


class ByteReader:
    def __init__(self, *chunks):
        self.chunks = list(chunks)

    async def read(self, size):
        assert size == 8192
        return self.chunks.pop(0) if self.chunks else b""


class FakeProcess:
    def __init__(self, stdout=(), stderr=(), returncode=0):
        self.stdout = ByteReader(*stdout)
        self.stderr = ByteReader(*stderr)
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.pid = 987654321
        self.wait = None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


@pytest.mark.parametrize(
    ("returncode", "ok"),
    [
        (0, True),
        (1, False),
        (-15, False),
    ],
)
def test_shell_result_status(returncode, ok):
    assert ShellResult(returncode).ok is ok


def test_structured_shell_stream_writes_base64_event():
    writer = StringIO()
    stream = StructuredShellStream(writer, "stderr")

    stream.write(b"\xff")
    stream.finish()

    assert json.loads(writer.getvalue()) == {
        "type": "shell-stream",
        "stream": "stderr",
        "encoding": "base64",
        "data": "/w==",
    }


@pytest.mark.asyncio
async def test_pump_shell_stream_forwards_chunks_and_finishes():
    sink = ByteSink()

    await _pump_shell_stream(ByteReader(b"one", b"two"), sink)

    assert sink.data == b"onetwo"
    assert sink.finished


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ('{"version":1,"command":"echo ok"}', "echo ok"),
        ('{"version":1,"command":"€"}', "€"),
    ],
)
def test_decode_shell_request(request_text, expected):
    assert decode_shell_request(request_text) == expected


@pytest.mark.parametrize(
    "request_text",
    [
        "{",
        "[]",
        '{"version":2,"command":"echo"}',
        '{"version":1}',
        '{"version":1,"command":1}',
        '{"version":1,"command":""}',
        '{"version":1,"command":"' + ("x" * 65537) + '"}',
    ],
)
def test_decode_shell_request_rejects_invalid_input(request_text):
    with pytest.raises((TypeError, ValueError)):
        decode_shell_request(request_text)


@pytest.mark.asyncio
async def test_run_shell_uses_separate_pipes_and_returns_status():
    process = FakeProcess(stdout=(b"out",), stderr=(b"err",), returncode=7)
    process.wait = lambda: asyncio.sleep(0, result=7)
    received = {}

    async def factory(command, **kwargs):
        received.update(command=command, **kwargs)
        return process

    stdout = ByteSink()
    stderr = ByteSink()
    returncode = await run_shell(
        "command",
        stdout,
        stderr,
        process_factory=factory,
    )

    assert returncode == 7
    assert stdout.data == b"out"
    assert stderr.data == b"err"
    assert received == {
        "command": "command",
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "start_new_session": os.name == "posix",
    }


@pytest.mark.asyncio
async def test_stop_shell_process_accepts_completed_process():
    process = FakeProcess(returncode=0)

    await _stop_shell_process(process)

    assert not process.terminated


@pytest.mark.asyncio
async def test_stop_shell_process_terminates_running_process():
    process = FakeProcess(returncode=None)

    async def wait():
        process.returncode = -15
        return -15

    process.wait = wait

    await _stop_shell_process(process)

    assert process.terminated
    assert not process.killed


def test_signal_shell_process_uses_direct_process_off_posix(mocker):
    process = FakeProcess(returncode=None)
    mocker.patch("herethere.here.shell.os.name", "nt")

    _signal_shell_process(process, signal.SIGTERM)

    assert process.terminated


def test_signal_shell_process_signals_posix_process_group(mocker):
    process = FakeProcess(returncode=None)
    killpg = mocker.patch("herethere.here.shell.os.killpg")

    _signal_shell_process(process, signal.SIGTERM)

    killpg.assert_called_once_with(process.pid, signal.SIGTERM)
    assert not process.terminated


@pytest.mark.asyncio
async def test_stop_shell_process_kills_process_after_timeout(mocker):
    process = FakeProcess(returncode=None)
    process.wait = mocker.AsyncMock(return_value=-9)

    async def timeout(awaitable, timeout):
        awaitable.close()
        raise asyncio.TimeoutError

    mocker.patch(
        "herethere.here.shell.asyncio.wait_for",
        side_effect=timeout,
    )

    await _stop_shell_process(process)

    assert process.terminated
    assert process.killed


@pytest.mark.asyncio
async def test_run_shell_stops_process_after_output_failure():
    process = FakeProcess(stdout=(b"out",), stderr=(), returncode=None)

    async def read_forever(size):
        await asyncio.Event().wait()

    process.stderr.read = read_forever

    async def wait():
        process.returncode = -15
        return -15

    process.wait = wait

    async def factory(command, **kwargs):
        return process

    with pytest.raises(OSError, match="closed"):
        await run_shell(
            "command",
            ByteSink(error=OSError("closed")),
            ByteSink(),
            process_factory=factory,
        )

    assert process.terminated


@pytest.mark.asyncio
async def test_run_shell_stops_process_on_cancellation():
    waiting = asyncio.Event()
    process = FakeProcess(returncode=None)

    async def read_forever(size):
        waiting.set()
        await asyncio.Event().wait()

    process.stdout.read = read_forever
    process.stderr.read = read_forever

    async def wait():
        process.returncode = -15
        return -15

    process.wait = wait

    async def factory(command, **kwargs):
        return process

    task = asyncio.create_task(
        run_shell(
            "command",
            ByteSink(),
            ByteSink(),
            process_factory=factory,
        )
    )
    await waiting.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated
