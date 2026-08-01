"""herethere.there.client"""

from __future__ import annotations

import asyncio
import base64
import binascii
import codecs
import contextlib
import json
import sys
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TextIO

import asyncssh

from herethere.everywhere.commands import (
    BACKGROUND_COMMAND,
    BACKGROUND_EXECUTE_COMMAND,
    BACKGROUND_VALUE_COMMAND,
    CODE_COMMAND,
    EXECUTE_COMMAND,
    PING_COMMAND,
    RECENT_LOGS_COMMAND,
    SHELL_COMMAND,
    VALUE_COMMAND,
)
from herethere.everywhere.config import ConnectionConfig
from herethere.everywhere.logging import logger
from herethere.everywhere.recent_logs import (
    DEFAULT_MAX_LOG_RECORDS,
    RECENT_LOGS_PROTOCOL_VERSION,
    RECENT_LOGS_RESPONSE_TYPE,
    RecentLogsSnapshot,
)
from herethere.everywhere.shell import (
    MAX_SHELL_COMMAND_BYTES,
    SHELL_PROTOCOL_VERSION,
    SHELL_RESULT_EVENT,
    SHELL_STREAM_EVENT,
    ShellResult,
)
from herethere.everywhere.values import loads_value


class ConnectionNotConfiguredError(Exception):
    """Connection configuration is missing."""


class ProtocolVersionError(RuntimeError):
    """The remote server does not support a required protocol command."""


class ProtocolError(RuntimeError):
    """The remote server returned malformed structured protocol data."""


@dataclass(frozen=True)
class RemoteError:
    """Structured error returned by a live-session operation."""

    remote_type: str
    message: str
    traceback: str


@dataclass(frozen=True)
class ExecutionResult:
    """Result of structured live-session code execution."""

    error: RemoteError | None = None

    @property
    def ok(self) -> bool:
        """Whether execution completed without a Python exception."""
        return self.error is None


def _forward_stream_event(
    event: dict,
    stdout: TextIO,
    stderr: TextIO,
) -> None:
    writer = {
        "stdout": stdout,
        "stderr": stderr,
    }.get(event.get("stream"))
    if writer is None or not isinstance(event.get("data"), str):
        raise ProtocolError("Remote server returned an invalid stream event.")
    writer.write(event["data"])
    if hasattr(writer, "flush"):
        writer.flush()


class _ShellOutput:
    """Forward raw shell bytes to bounded or ordinary text writers."""

    def __init__(self, writer: TextIO):
        self.writer = writer
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def write(self, data: bytes) -> None:
        """Write bytes without losing byte counts in bounded collectors."""
        write_bytes = getattr(self.writer, "write_bytes", None)
        if write_bytes is not None:
            write_bytes(data)
        else:
            text = self.decoder.decode(data)
            if text:
                self.writer.write(text)
        if hasattr(self.writer, "flush"):
            self.writer.flush()

    def finish(self) -> None:
        """Flush a trailing partial UTF-8 sequence to an ordinary writer."""
        if getattr(self.writer, "write_bytes", None) is not None:
            return
        text = self.decoder.decode(b"", final=True)
        if text:
            self.writer.write(text)
        if hasattr(self.writer, "flush"):
            self.writer.flush()


class PersistentConnection(AbstractAsyncContextManager):
    """SSH connection async context manager with automatic reconnection."""

    def __init__(self):
        self.config: ConnectionConfig | None = None
        self.connection: asyncssh.SSHClientConnection | None = None

    async def __aenter__(self):
        return await self.ensure_connected()

    async def ensure_connected(self):
        """Return an active SSH connection, reconnecting if needed."""
        if await self.check_connection():
            return self.connection
        if self.connection:
            self.close()
        return await self.reconnect()

    async def __aexit__(self, *exc_info):
        pass

    def close(self):
        """Close current connection."""
        if self.connection:
            try:
                self.connection.close()
            except asyncssh.Error:
                pass
        self.connection = None

    async def configure(self, config: ConnectionConfig):
        """Apply new connection config."""
        self.close()
        self.config = config
        return await self.ensure_connected()

    async def check_connection(self) -> bool:
        """Check connection is active."""
        if self.connection:
            try:
                await self.ping()
            except (asyncssh.Error, ProtocolError):
                logger.debug("SSH connection ping failed.")
            else:
                return True
        return False

    async def ping(self) -> str:
        """Run and validate one ping on the current connection."""
        ssh = self.connection
        if ssh is None:
            ssh = await self.reconnect()

        async with ssh.create_process(PING_COMMAND, encoding=None) as process:
            try:
                stdout, stderr = await asyncio.gather(
                    process.stdout.read(),
                    process.stderr.read(),
                )
                result = await process.wait()
            except asyncio.CancelledError:
                with contextlib.suppress(OSError):
                    process.terminate()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), timeout=1)
                raise

        if result.returncode != 0 or stdout != b"pong" or stderr:
            raise ProtocolError("Remote server returned an invalid ping response.")
        return "pong"

    async def reconnect(self):
        """Establish connection."""
        if not self.config:
            raise ConnectionNotConfiguredError("Connection is not configured.")
        self.connection = await asyncssh.connect(**self.config.asdict, known_hosts=None)
        return self.connection


class Client:
    """Client for remote interpreter."""

    def __init__(self):
        self.connection = PersistentConnection()
        self.structured_protocol: bool | None = None

    async def copy(self) -> Client:
        """Return a copy of the configured connection."""
        client = Client()
        await client.connect(self.connection.config)
        client.structured_protocol = self.structured_protocol
        return client

    async def connect(self, config: ConnectionConfig):
        """Connect to remote."""
        self.structured_protocol = None
        await self.connection.configure(config)

    async def disconnect(self):
        """Disconnect from the remote."""
        self.connection.close()

    async def ping(self) -> str:
        """Return ``pong`` after validating the remote ping response."""
        return await self.connection.ping()

    async def runcode(
        self,
        code: str,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> str:
        """Execute python code on the remote side."""
        try:
            await self.execute(code, stdout, stderr)
        except ProtocolVersionError:
            await self._execute_code(CODE_COMMAND, code, stdout, stderr)

    async def runcode_background(
        self,
        code: str,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> str:
        """Execute Python code in a separate thread on the remote side."""
        await self._execute_code(BACKGROUND_COMMAND, code, stdout, stderr)

    async def shell(
        self,
        code: str,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> str:
        """Execute shell command on the remote side."""
        output = stdout or sys.stdout
        await self.execute_shell(code, stdout=output, stderr=output)

    async def execute_shell(
        self,
        command: str,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> ShellResult:
        """Execute a shell command with structured streams and status."""
        command_bytes = len(command.encode("utf-8"))
        if not command or command_bytes > MAX_SHELL_COMMAND_BYTES:
            raise ValueError(
                f"command must contain 1..{MAX_SHELL_COMMAND_BYTES} UTF-8 bytes"
            )
        request = json.dumps(
            {
                "version": SHELL_PROTOCOL_VERSION,
                "command": command,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        event = await self._shell_operation(
            request,
            stdout=stdout,
            stderr=stderr,
        )
        return _shell_result(event)

    async def get(self, expression: str):
        """Evaluate a Python expression remotely and return its Python value."""
        return await self._get_value(expression)

    async def get_background(self, expression: str):
        """Evaluate a Python expression in a remote executor worker."""
        return await self._get_value(
            expression,
            command=BACKGROUND_VALUE_COMMAND,
            background=True,
        )

    async def logs(self, max_records: int | None = None) -> RecentLogsSnapshot:
        """Return a finite snapshot of recent remote Python log records."""
        if max_records is not None and (
            not isinstance(max_records, int)
            or isinstance(max_records, bool)
            or not 1 <= max_records <= DEFAULT_MAX_LOG_RECORDS
        ):
            raise ValueError(
                f"max_records must be in the range 1..{DEFAULT_MAX_LOG_RECORDS}"
            )
        request = json.dumps(
            {
                "version": RECENT_LOGS_PROTOCOL_VERSION,
                "records": max_records,
            },
            separators=(",", ":"),
        )
        async with self.connection as ssh:
            async with ssh.create_process(RECENT_LOGS_COMMAND) as process:
                process.stdin.write(request)
                process.stdin.write_eof()
                try:
                    message, diagnostic = await asyncio.gather(
                        process.stdout.read(),
                        process.stderr.read(),
                    )
                    await process.wait()
                except asyncio.CancelledError:
                    process.terminate()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(process.wait(), timeout=1)
                    raise

        if not message:
            if "Unknown command" in diagnostic:
                raise _logs_protocol_version_error()
            detail = f" Remote stderr: {diagnostic}" if diagnostic else ""
            raise ProtocolError(f"Remote logs command returned no output.{detail}")
        if diagnostic:
            raise ProtocolError(f"Remote logs command failed: {diagnostic}")
        try:
            event = json.loads(message)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProtocolError(
                "Remote server returned malformed recent-log data."
            ) from exc
        return _recent_logs_snapshot(event)

    async def _get_value(
        self,
        expression: str,
        *,
        command: str = VALUE_COMMAND,
        background: bool = False,
    ):
        """Run a value command and decode its response."""
        async with self.connection as ssh:
            async with ssh.create_process(command) as process:
                process.stdin.write(expression)
                process.stdin.write_eof()

                message = await process.stdout.read()
                await process.wait()

                if not message:
                    stderr = await process.stderr.read()
                    if background and "Unknown command" in stderr:
                        raise _background_protocol_version_error()
                    message = "Remote value command returned no output."
                    if stderr:
                        message += f"\nRemote stderr:\n{stderr}"
                    raise RuntimeError(message)

                return loads_value(message)

    async def execute(
        self,
        code: str,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> ExecutionResult:
        """Execute code using the structured live-session protocol."""
        if self.structured_protocol is False:
            raise _protocol_version_error()
        try:
            event = await self._structured_operation(
                EXECUTE_COMMAND,
                code,
                stdout=stdout,
                stderr=stderr,
            )
        except ProtocolVersionError:
            self.structured_protocol = False
            raise
        self.structured_protocol = True
        return ExecutionResult(error=_remote_error(event))

    async def execute_background(
        self,
        code: str,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> ExecutionResult:
        """Execute code in a remote worker using the structured protocol."""
        event = await self._structured_operation(
            BACKGROUND_EXECUTE_COMMAND,
            code,
            stdout=stdout,
            stderr=stderr,
            background=True,
        )
        return ExecutionResult(error=_remote_error(event))

    async def upload(self, localpaths: list[str], remotepath) -> None:
        """Upload files and directories to remote via SFTP."""
        async with self.connection as ssh:
            async with ssh.start_sftp_client() as sftp:
                await sftp.put(
                    localpaths=localpaths,
                    remotepath=remotepath,
                    recurse=True,
                    progress_handler=self.sftp_progress_handler,
                )

    async def download(self, remotepaths: list[str], localpath) -> None:
        """Download files and directories from remote via SFTP."""
        async with self.connection as ssh:
            async with ssh.start_sftp_client() as sftp:
                await sftp.get(
                    remotepaths=remotepaths,
                    localpath=localpath,
                    recurse=True,
                    sparse=False,
                    block_size=256 * 1024,
                    max_requests=16,
                    progress_handler=self.sftp_progress_handler,
                )

    def sftp_progress_handler(self, srcpath, dstpath, copied, total):
        """Log SFTP transfer progress."""
        percent = copied / total * 100 if total else 100
        logger.debug(
            "SFTP progress: %s -> %s: %s/%s bytes (%.1f%%)",
            srcpath,
            dstpath,
            copied,
            total,
            percent,
        )

    async def _execute_code(
        self,
        command: str,
        code: str,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ):
        """Execute command with a code on the remote side."""

        if stdout is None:
            stdout = sys.stdout
        if stderr is None:
            stderr = sys.stderr

        async with self.connection as ssh:
            async with ssh.create_process(command) as process:
                process.stdin.write(code)
                # Remote handlers read the submitted code from stdin. Signal
                # end-of-input so they can start or finish execution instead
                # of waiting for more code.
                process.stdin.write_eof()

                async def forward_output(reader, writer):
                    # Stream line-by-line for long-running commands such as
                    # `%there log`. readline() also returns the final partial
                    # line at EOF, so output without a trailing newline is kept.
                    while data := await reader.readline():
                        writer.write(data)
                        if hasattr(writer, "flush"):
                            writer.flush()

                try:
                    await asyncio.gather(
                        forward_output(process.stdout, stdout),
                        forward_output(process.stderr, stderr),
                    )
                    await process.wait()
                except asyncio.CancelledError:
                    process.terminate()
                    # Try to close the remote channel cleanly, but keep
                    # cancellation bounded from the caller's perspective.
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(process.wait(), timeout=1)
                    raise

    async def _structured_operation(
        self,
        command: str,
        payload: str,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        background: bool = False,
    ) -> dict:
        """Run a structured JSON-lines command and return its final event."""
        stdout = stdout or sys.stdout
        stderr = stderr or sys.stderr
        async with self.connection as ssh:
            async with ssh.create_process(command) as process:
                process.stdin.write(payload)
                process.stdin.write_eof()
                final_event = None
                diagnostic_stderr: list[str] = []

                async def read_events():
                    nonlocal final_event
                    while line := await process.stdout.readline():
                        try:
                            event = json.loads(line)
                        except (TypeError, json.JSONDecodeError) as exc:
                            raise ProtocolError(
                                "Remote server returned malformed protocol output."
                            ) from exc
                        if not isinstance(event, dict):
                            raise ProtocolError(
                                "Remote server returned a non-object protocol event."
                            )
                        event_type = event.get("type")
                        if event_type == "stream":
                            _forward_stream_event(event, stdout, stderr)
                        elif event_type == "result":
                            if final_event is not None:
                                raise ProtocolError(
                                    "Remote server returned multiple result events."
                                )
                            final_event = event
                        else:
                            raise ProtocolError(
                                "Remote server returned an unknown protocol event."
                            )

                async def read_diagnostics():
                    while data := await process.stderr.readline():
                        diagnostic_stderr.append(data)

                try:
                    await asyncio.gather(read_events(), read_diagnostics())
                    await process.wait()
                except asyncio.CancelledError:
                    process.terminate()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(process.wait(), timeout=1)
                    raise

                if final_event is None:
                    diagnostic = "".join(diagnostic_stderr)
                    if "Unknown command" in diagnostic:
                        raise _protocol_version_error_for(background)
                    raise ProtocolError("Remote protocol returned no result event.")
                if diagnostic_stderr:
                    stderr.write("".join(diagnostic_stderr))
                    if hasattr(stderr, "flush"):
                        stderr.flush()
                if not isinstance(final_event.get("ok"), bool):
                    raise ProtocolError("Remote result event has no valid status.")
                return final_event

    async def _shell_operation(
        self,
        payload: str,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> dict:
        """Run the structured shell protocol and return its final event."""
        outputs = {
            "stdout": _ShellOutput(stdout or sys.stdout),
            "stderr": _ShellOutput(stderr or sys.stderr),
        }
        async with self.connection as ssh:
            async with ssh.create_process(SHELL_COMMAND) as process:
                process.stdin.write(payload)
                process.stdin.write_eof()
                final_event = None
                diagnostic_stderr: list[str] = []

                async def read_events():
                    nonlocal final_event
                    while line := await process.stdout.readline():
                        try:
                            event = json.loads(line)
                        except (TypeError, json.JSONDecodeError) as exc:
                            raise ProtocolError(
                                "Remote server returned malformed shell protocol "
                                "output."
                            ) from exc
                        if not isinstance(event, dict):
                            raise ProtocolError(
                                "Remote server returned a non-object shell event."
                            )
                        if final_event is not None:
                            raise ProtocolError(
                                "Remote server returned an event after the final "
                                "shell result."
                            )
                        event_type = event.get("type")
                        if event_type == SHELL_STREAM_EVENT:
                            _forward_shell_stream_event(event, outputs)
                        elif event_type == SHELL_RESULT_EVENT:
                            final_event = event
                        else:
                            raise ProtocolError(
                                "Remote server returned an unknown shell event."
                            )

                async def read_diagnostics():
                    while data := await process.stderr.readline():
                        diagnostic_stderr.append(data)

                try:
                    await asyncio.gather(read_events(), read_diagnostics())
                    await process.wait()
                except asyncio.CancelledError:
                    process.terminate()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(process.wait(), timeout=1)
                    raise
                finally:
                    for output in outputs.values():
                        output.finish()

                if final_event is None:
                    diagnostic = "".join(diagnostic_stderr)
                    if "request must use protocol version" in diagnostic:
                        raise _shell_protocol_version_error("unknown")
                    detail = f" Remote stderr: {diagnostic}" if diagnostic else ""
                    raise ProtocolError(
                        f"Remote shell protocol returned no result.{detail}"
                    )
                if diagnostic_stderr:
                    raise ProtocolError(
                        f"Remote shell command failed: {''.join(diagnostic_stderr)}"
                    )
                return final_event


def _protocol_version_error() -> ProtocolVersionError:
    return ProtocolVersionError(
        "The remote server does not support structured live execution. "
        "Upgrade herethere on the remote server."
    )


def _background_protocol_version_error() -> ProtocolVersionError:
    return ProtocolVersionError(
        "The remote server does not support background execution. "
        "Upgrade herethere on the remote server."
    )


def _protocol_version_error_for(background: bool) -> ProtocolVersionError:
    if background:
        return _background_protocol_version_error()
    return _protocol_version_error()


def _shell_protocol_version_error(remote_version: object) -> ProtocolVersionError:
    return ProtocolVersionError(
        f"The remote shell protocol is not compatible with version "
        f"{SHELL_PROTOCOL_VERSION}. Remote version: {remote_version!r}."
    )


def _forward_shell_stream_event(
    event: dict,
    outputs: dict[str, _ShellOutput],
) -> None:
    """Decode and forward one raw shell output event."""
    output = outputs.get(event.get("stream"))
    data = event.get("data")
    if output is None or event.get("encoding") != "base64" or not isinstance(data, str):
        raise ProtocolError("Remote server returned an invalid shell stream event.")
    try:
        decoded = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProtocolError(
            "Remote server returned invalid base64 shell output."
        ) from exc
    output.write(decoded)


def _shell_result(event: dict) -> ShellResult:
    """Validate and decode a structured shell completion event."""
    if event.get("version") != SHELL_PROTOCOL_VERSION:
        raise _shell_protocol_version_error(event.get("version"))
    ok = event.get("ok")
    returncode = event.get("returncode")
    if (
        not isinstance(ok, bool)
        or not isinstance(returncode, int)
        or isinstance(returncode, bool)
        or ok != (returncode == 0)
    ):
        raise ProtocolError("Remote server returned invalid shell result fields.")
    error = event.get("error")
    if ok:
        if error is not None:
            raise ProtocolError("Successful remote shell result included an error.")
    elif (
        not isinstance(error, dict)
        or error.get("type") != "ShellExitError"
        or not isinstance(error.get("message"), str)
    ):
        raise ProtocolError("Failed remote shell result has no valid error.")
    return ShellResult(returncode=returncode)


def _logs_protocol_version_error() -> ProtocolVersionError:
    return ProtocolVersionError(
        "The remote server does not support recent-log snapshots. "
        "Upgrade herethere on the remote server."
    )


def _recent_logs_snapshot(event: object) -> RecentLogsSnapshot:
    """Validate and decode a recent-log protocol response."""
    if (
        not isinstance(event, dict)
        or event.get("type") != RECENT_LOGS_RESPONSE_TYPE
        or event.get("version") != RECENT_LOGS_PROTOCOL_VERSION
    ):
        raise ProtocolError("Remote server returned invalid recent-log data.")
    text = event.get("text")
    byte_count = event.get("bytes")
    record_count = event.get("records")
    truncated = event.get("truncated")
    if not isinstance(text, str):
        raise ProtocolError("Remote server returned invalid recent-log fields.")
    if (
        not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or byte_count < 0
    ):
        raise ProtocolError("Remote server returned invalid recent-log fields.")
    if not isinstance(truncated, bool):
        raise ProtocolError("Remote server returned invalid recent-log fields.")
    if (
        not isinstance(record_count, int)
        or isinstance(record_count, bool)
        or record_count < 0
    ):
        raise ProtocolError("Remote server returned invalid recent-log fields.")
    if byte_count != len(text.encode("utf-8")):
        raise ProtocolError("Remote server returned invalid recent-log fields.")
    return RecentLogsSnapshot(
        text=text,
        bytes=byte_count,
        records=record_count,
        truncated=truncated,
    )


def _remote_error(event: dict) -> RemoteError | None:
    """Decode an optional structured remote error."""
    if event["ok"]:
        return None
    error = event.get("error")
    if not isinstance(error, dict):
        raise ProtocolError("Remote failure result has no error details.")
    return RemoteError(
        remote_type=str(error.get("remote_type", "Exception")),
        message=str(error.get("message", "")),
        traceback=str(error.get("traceback", "")),
    )
