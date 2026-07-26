"""Shared shell subprocess execution and SSH protocol adapters."""

import asyncio
import base64
import contextlib
import os
import signal
from asyncio.subprocess import Process
from collections.abc import Callable
from typing import Protocol

import asyncssh

from herethere.everywhere.protocol import decode_request_object, write_event
from herethere.everywhere.shell import (
    MAX_SHELL_COMMAND_BYTES,
    SHELL_PROTOCOL_VERSION,
    SHELL_RESULT_EVENT,
    SHELL_STREAM_EVENT,
)

MAX_SHELL_REQUEST_LENGTH = MAX_SHELL_COMMAND_BYTES * 6 + 1024
SHELL_TERMINATE_TIMEOUT = 1.0


class ByteSink(Protocol):
    """Destination for chunks read from a shell subprocess pipe."""

    def write(self, data: bytes) -> None:
        """Write one byte chunk."""

    def finish(self) -> None:
        """Flush any buffered terminal data."""


class StructuredShellStream:
    """Encode shell bytes as one structured stream of protocol events."""

    def __init__(self, writer, stream: str):
        self.writer = writer
        self.stream = stream

    def write(self, data: bytes) -> None:
        """Write one base64-encoded shell output event."""
        write_event(
            self.writer,
            {
                "type": SHELL_STREAM_EVENT,
                "stream": self.stream,
                "encoding": "base64",
                "data": base64.b64encode(data).decode("ascii"),
            },
        )

    def finish(self) -> None:
        """Structured byte chunks do not require decoder finalization."""


async def _pump_shell_stream(reader: asyncio.StreamReader, sink: ByteSink) -> None:
    """Forward one subprocess pipe until EOF."""
    while data := await reader.read(8192):
        sink.write(data)
    sink.finish()


async def _stop_shell_process(process: Process) -> None:
    """Stop a shell subprocess without allowing cancellation to hang."""
    if process.returncode is not None:
        return
    _signal_shell_process(process, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=SHELL_TERMINATE_TIMEOUT)
    except asyncio.TimeoutError:
        _signal_shell_process(process, signal.SIGKILL)
        await process.wait()


def _signal_shell_process(
    process: Process,
    shell_signal: signal.Signals,
) -> None:
    """Signal the shell process group when supported, or the shell itself."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, shell_signal)
        except (ProcessLookupError, PermissionError):
            pass
        else:
            return
    with contextlib.suppress(ProcessLookupError):
        if shell_signal == signal.SIGKILL:
            process.kill()
        else:
            process.terminate()


async def run_shell(
    command: str,
    stdout: ByteSink,
    stderr: ByteSink,
    *,
    process_factory: Callable = asyncio.create_subprocess_shell,
) -> int:
    """Execute a shell command once and pump its output to byte sinks."""
    process = await process_factory(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    pumps = [
        asyncio.create_task(
            _pump_shell_stream(process.stdout, stdout),
            name="remote shell stdout",
        )
    ]
    pumps.append(
        asyncio.create_task(
            _pump_shell_stream(process.stderr, stderr),
            name="remote shell stderr",
        )
    )
    try:
        await asyncio.gather(*pumps)
        return await process.wait()
    except asyncio.CancelledError:
        await _stop_shell_process(process)
        raise
    except Exception:  # pylint: disable=broad-exception-caught
        await _stop_shell_process(process)
        raise
    finally:
        for task in pumps:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)


def decode_shell_request(request_text: str) -> str:
    """Validate and return a structured shell command request."""
    request = decode_request_object(request_text)
    if request.get("version") != SHELL_PROTOCOL_VERSION:
        raise ValueError(f"request must use protocol version {SHELL_PROTOCOL_VERSION}")
    command = request.get("command")
    if not isinstance(command, str) or not command:
        raise ValueError("command must be a non-empty string")
    command_bytes = len(command.encode("utf-8"))
    if command_bytes > MAX_SHELL_COMMAND_BYTES:
        raise ValueError(
            f"command is too large ({command_bytes} bytes > "
            f"{MAX_SHELL_COMMAND_BYTES} bytes)"
        )
    return command


async def handle_shell_command(
    process: asyncssh.SSHServerProcess,
    namespace: dict,
) -> None:
    """Execute a structured shell request and return its status."""
    request_text = await process.stdin.read(MAX_SHELL_REQUEST_LENGTH)
    try:
        command = decode_shell_request(request_text)
    except (TypeError, ValueError) as exc:
        process.stderr.write(f"Invalid shell request: {exc}")
        return
    returncode = await run_shell(
        command,
        StructuredShellStream(process.stdout, "stdout"),
        StructuredShellStream(process.stdout, "stderr"),
    )
    event = {
        "type": SHELL_RESULT_EVENT,
        "version": SHELL_PROTOCOL_VERSION,
        "ok": returncode == 0,
        "returncode": returncode,
    }
    if returncode:
        event["error"] = {
            "type": "ShellExitError",
            "message": f"remote shell exited with status {returncode}",
        }
    write_event(process.stdout, event)


__all__ = (
    "MAX_SHELL_REQUEST_LENGTH",
    "SHELL_TERMINATE_TIMEOUT",
    "StructuredShellStream",
    "decode_shell_request",
    "handle_shell_command",
    "run_shell",
)
