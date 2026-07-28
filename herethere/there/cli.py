"""Standalone command-line interface for herethere."""

# pylint: disable=too-many-lines

import ast
import asyncio
import io
import json
import sys
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from enum import IntEnum
from importlib import metadata
from pathlib import Path
from typing import Any, TextIO, cast

import asyncssh
import click

from herethere.everywhere.config import ConnectionConfig, ConnectionConfigError
from herethere.everywhere.recent_logs import DEFAULT_MAX_LOG_RECORDS
from herethere.everywhere.shell import MAX_SHELL_COMMAND_BYTES
from herethere.everywhere.values import RemoteValueError
from herethere.there.client import (
    Client,
    ProtocolError,
    ProtocolVersionError,
    RemoteError,
)

DEFAULT_MAX_OUTPUT = 64 * 1024
MAX_MAX_OUTPUT = 1024 * 1024
MAX_CODE_BYTES = 64 * 1024
DEFAULT_PING_TIMEOUT = 10.0
PLUGIN_GROUP = "herethere.cli"


class ExitCode(IntEnum):
    """Stable process exit codes used by the console CLI."""

    SUCCESS = 0
    USAGE = 2
    CONNECTION = 3
    REMOTE = 4
    LOCAL_IO = 5
    TIMEOUT = 124


class CLIError(Exception):
    """An expected CLI operation failure."""

    exit_code = ExitCode.REMOTE
    error_type = "RemoteOperationError"
    phase: str | None = None

    def __init__(
        self,
        message: str,
        *,
        phase: str | None = None,
        details: dict[str, Any] | None = None,
        result_fields: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        if phase is not None:
            self.phase = phase
        self.details = details or {}
        self.result_fields = result_fields or {}


class ConnectionFailure(CLIError):
    """A connection or authentication failure."""

    exit_code = ExitCode.CONNECTION
    error_type = "ConnectionError"
    phase = "connection"


class LocalIOError(CLIError):
    """A local filesystem or stream failure."""

    exit_code = ExitCode.LOCAL_IO
    error_type = "LocalIOError"
    phase = "local"


class PluginError(CLIError):
    """A plugin discovery or loading failure."""

    exit_code = ExitCode.USAGE
    error_type = "PluginError"
    phase = "plugin"


class RemoteOperationError(CLIError):
    """An expected remote operation failure."""

    phase = "operation"


class SFTPFailure(RemoteOperationError):
    """An SFTP operation failed on the remote target."""

    error_type = "SFTPError"
    phase = "transfer"


class ValueSerializationFailure(RemoteOperationError):
    """A returned Python value cannot be represented in strict JSON."""

    error_type = "ValueSerializationError"
    phase = "serialization"


class RemoteExecutionFailure(RemoteOperationError):
    """Python execution or evaluation failed in the live interpreter."""

    error_type = "RemoteExecutionError"
    phase = "remote_execution"

    def __init__(self, error: RemoteError):
        super().__init__(
            error.message,
            details={
                "remote_type": error.remote_type,
                "traceback": error.traceback,
            },
        )


class RemoteShellFailure(RemoteOperationError):
    """A remote shell subprocess returned a non-zero status."""

    error_type = "RemoteShellError"
    phase = "shell_execution"

    def __init__(self, returncode: int):
        message = f"remote shell exited with status {returncode}"
        super().__init__(
            message,
            details={"returncode": returncode},
            result_fields={"returncode": returncode},
        )


class ProtocolVersionFailure(RemoteOperationError):
    """The remote server is too old for a structured operation."""

    error_type = "ProtocolVersionError"
    phase = "remote_execution"


class OperationTimeout(CLIError):
    """A connection or remote execution exceeded its total time budget."""

    exit_code = ExitCode.TIMEOUT
    error_type = "TimeoutError"


@dataclass(frozen=True)
class _ErrorRule:
    exception_type: type[Exception]
    exit_code: ExitCode
    error_type: str | None
    phase: str
    message: Callable[[Exception], str] = str

    def describe(self, error: Exception) -> tuple[ExitCode, dict[str, Any]]:
        return self.exit_code, {
            "type": self.error_type or type(error).__name__,
            "phase": self.phase,
            "message": self.message(error),
        }


def _click_message(error: Exception) -> str:
    return error.format_message()


def _timeout_message(error: Exception) -> str:
    return str(error) or "operation timed out"


_ERROR_RULES = (
    _ErrorRule(
        click.UsageError,
        ExitCode.USAGE,
        "UsageError",
        "usage",
        _click_message,
    ),
    _ErrorRule(
        ConnectionConfigError,
        ExitCode.USAGE,
        "ConfigError",
        "config",
    ),
    _ErrorRule(
        TimeoutError,
        ExitCode.TIMEOUT,
        "TimeoutError",
        "connection",
        _timeout_message,
    ),
    _ErrorRule(
        asyncssh.PermissionDenied,
        ExitCode.CONNECTION,
        "AuthenticationError",
        "authentication",
    ),
    _ErrorRule(
        asyncssh.DisconnectError,
        ExitCode.CONNECTION,
        "ConnectionError",
        "connection",
    ),
    _ErrorRule(
        click.ClickException,
        ExitCode.REMOTE,
        None,
        "operation",
        _click_message,
    ),
    _ErrorRule(
        Exception,
        ExitCode.REMOTE,
        "InternalError",
        "internal",
    ),
)


class BoundedTextCollector(io.TextIOBase):
    """Collect the tail of UTF-8 text while counting all bytes written."""

    def __init__(self, limit: int = DEFAULT_MAX_OUTPUT):
        if not 1 <= limit <= MAX_MAX_OUTPUT:
            raise ValueError(f"limit must be in the range 1..{MAX_MAX_OUTPUT}")
        self.limit = limit
        self._tail = bytearray()
        self.byte_count = 0

    @property
    def encoding(self) -> str:
        return "utf-8"

    @property
    def truncated(self) -> bool:
        return self.byte_count > len(self._tail)

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("write() argument must be str")
        data = text.encode("utf-8")
        self.write_bytes(data)
        return len(text)

    def write_bytes(self, data: bytes) -> None:
        """Add bytes directly, truncating before UTF-8 decoding."""
        self.byte_count += len(data)
        if len(data) >= self.limit:
            self._tail[:] = data[-self.limit :]
            return
        excess = len(self._tail) + len(data) - self.limit
        if excess > 0:
            del self._tail[:excess]
        self._tail.extend(data)

    def set_limit(self, limit: int) -> None:
        """Change the retained-byte limit and trim the existing tail."""
        if not 1 <= limit <= MAX_MAX_OUTPUT:
            raise ValueError(f"limit must be in the range 1..{MAX_MAX_OUTPUT}")
        self.limit = limit
        if len(self._tail) > limit:
            del self._tail[:-limit]

    def getvalue(self) -> str:
        return bytes(self._tail).decode("utf-8", errors="replace")

    def metadata(self, stream: str) -> dict[str, Any]:
        """Return envelope fields for one captured stream."""
        return {
            stream: self.getvalue(),
            f"{stream}_bytes": self.byte_count,
            f"{stream}_truncated": self.truncated,
        }


@dataclass
class CLIContext:
    """Values selected by root-level CLI options."""

    output_format: str = "text"
    config: Path | None = None
    timeout: float | None = None
    max_output: int = DEFAULT_MAX_OUTPUT


@dataclass
class _CapturedInvocation:
    stdout: BoundedTextCollector
    stderr: BoundedTextCollector
    result: Any = None
    error: Exception | None = None


def load_connection_config(config: str | Path | None = None) -> ConnectionConfig:
    """Load the existing ``there`` connection configuration."""
    path = str(config) if config is not None else None
    return ConnectionConfig.load(prefix="there", path=path)


def get_cli_context(ctx: click.Context) -> CLIContext:
    """Return the typed invocation context owned by the root command."""
    invocation = ctx.find_root().obj
    if not isinstance(invocation, CLIContext):
        raise TypeError("root CLI context is not initialized")
    return invocation


def _text_streams(ctx: click.Context) -> tuple[TextIO, TextIO]:
    """Return stdout and stderr outside the root command's JSON capture."""
    group = cast(PluginGroup, ctx.find_root().command)
    return group.invocation_streams()


def _entry_points() -> tuple[Any, ...]:
    try:
        return tuple(metadata.entry_points(group=PLUGIN_GROUP))
    except TypeError:  # pragma: no cover - Python/importlib compatibility
        return tuple(metadata.entry_points().select(group=PLUGIN_GROUP))
    except Exception as exc:
        raise PluginError(f"Could not discover CLI plugins: {exc}") from exc


class PluginGroup(click.Group):
    """Click group which discovers plugin names without importing plugins."""

    _command_name = ""
    _json_requested = False
    _output_format = "text"
    _stdout_collector: BoundedTextCollector | None = None
    _stderr_collector: BoundedTextCollector | None = None
    _invocation_stdout: TextIO | None = None
    _invocation_stderr: TextIO | None = None

    def _plugins(self) -> dict[str, tuple[Any, ...]]:
        plugins: dict[str, list[Any]] = {}
        for entry_point in _entry_points():
            plugins.setdefault(entry_point.name, []).append(entry_point)
        return {name: tuple(items) for name, items in plugins.items()}

    def list_commands(self, ctx: click.Context) -> list[str]:
        names = set(super().list_commands(ctx))
        names.update(self._plugins())
        return sorted(names)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        self._command_name = cmd_name
        builtin = super().get_command(ctx, cmd_name)
        if builtin is not None:
            return builtin

        matches = self._plugins().get(cmd_name, ())
        if not matches:
            return None
        if len(matches) > 1:
            raise PluginError(
                f"Duplicate {PLUGIN_GROUP} entry points named {cmd_name!r}."
            )
        try:
            command = matches[0].load()
        except Exception as exc:
            raise PluginError(f"Could not load plugin {cmd_name!r}: {exc}") from exc
        if not isinstance(command, click.Command):
            raise PluginError(f"Plugin {cmd_name!r} did not load a click.Command.")
        return command

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter):
        rows = []
        plugins = self._plugins()
        for name in super().list_commands(ctx):
            command = self.commands[name]
            if not command.hidden:
                rows.append((name, command.get_short_help_str()))
        for name in sorted(plugins):
            if name not in self.commands:
                rows.append((name, "[plugin]"))
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(sorted(rows))

    def main(
        self,
        args: list[str] | tuple[str, ...] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        **extra: Any,
    ) -> Any:
        argv = list(args) if args is not None else sys.argv[1:]
        self._command_name = ""
        self._json_requested = False
        self._output_format = "text"
        captured = self._invoke_captured(
            argv=argv,
            prog_name=prog_name,
            complete_var=complete_var,
            extra=extra,
        )
        normal_help = "--help" in argv or "-h" in argv

        json_mode = self._json_requested or self._output_format == "json"
        if not json_mode or normal_help:
            click.echo(captured.stdout.getvalue(), nl=False)
            click.echo(captured.stderr.getvalue(), nl=False, err=True)
            return _finish_text_mode(
                captured.error,
                captured.result,
                standalone_mode,
            )

        exit_code, error_data = _error_details(captured.error)
        envelope = {
            "ok": captured.error is None,
            "command": self._command_name,
            "exit_code": int(exit_code),
            **captured.stdout.metadata("stdout"),
            **captured.stderr.metadata("stderr"),
            "error": error_data,
        }
        if isinstance(captured.result, dict):
            envelope.update(
                (key, value)
                for key, value in captured.result.items()
                if key not in envelope
            )
        if isinstance(captured.error, CLIError):
            envelope.update(
                (key, value)
                for key, value in captured.error.result_fields.items()
                if key not in envelope
            )
        click.echo(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
        if standalone_mode:
            if exit_code:
                raise SystemExit(int(exit_code))
            return None
        return int(exit_code)

    def _invoke_captured(
        self,
        *,
        argv: list[str],
        prog_name: str | None,
        complete_var: str | None,
        extra: dict[str, Any],
    ) -> _CapturedInvocation:
        """Invoke Click while collecting output for a possible JSON envelope."""
        self._invocation_stdout = sys.stdout
        self._invocation_stderr = sys.stderr
        captured = _CapturedInvocation(
            stdout=BoundedTextCollector(),
            stderr=BoundedTextCollector(),
        )
        self._stdout_collector = captured.stdout
        self._stderr_collector = captured.stderr
        try:
            with redirect_stdout(captured.stdout), redirect_stderr(captured.stderr):
                captured.result = super().main(
                    args=argv,
                    prog_name=prog_name,
                    complete_var=complete_var,
                    standalone_mode=False,
                    **extra,
                )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            captured.error = exc
        finally:
            self._stdout_collector = None
            self._stderr_collector = None
            self._invocation_stdout = None
            self._invocation_stderr = None
        return captured

    def invocation_streams(self) -> tuple[TextIO, TextIO]:
        """Return the saved streams used for live, unbounded text output."""
        if self._invocation_stdout is None or self._invocation_stderr is None:
            raise RuntimeError("CLI invocation streams are not available.")
        return self._invocation_stdout, self._invocation_stderr


def _finish_text_mode(
    error: Exception | None, result: Any, standalone_mode: bool
) -> Any:
    if error is None:
        return result
    if isinstance(error, click.ClickException):
        error.show()
        exit_code = error.exit_code
    elif isinstance(error, (CLIError, ConnectionConfigError, TimeoutError)):
        exit_code, error_data = _error_details(error)
        click.echo(f"Error: {error_data['message']}", err=True)
    else:
        raise error
    if standalone_mode:
        raise SystemExit(int(exit_code)) from error
    return int(exit_code)


def _set_output_format(ctx: click.Context, param: click.Parameter, value: str) -> str:
    del param
    group = ctx.command
    if isinstance(group, PluginGroup):
        group._output_format = value
        if (
            value == "text"
            and group._json_requested
            and ctx.get_parameter_source("output_format")
            is click.core.ParameterSource.COMMANDLINE
        ):
            raise click.UsageError("--json cannot be used with --format text.")
    return value


def _set_json_requested(
    ctx: click.Context, param: click.Parameter, value: bool
) -> bool:
    del param
    group = ctx.command
    if isinstance(group, PluginGroup) and value:
        group._json_requested = True
    return value


def _set_max_output(ctx: click.Context, param: click.Parameter, value: int) -> int:
    del param
    group = ctx.find_root().command
    if isinstance(group, PluginGroup):
        for collector in (group._stdout_collector, group._stderr_collector):
            if collector is not None:
                collector.set_limit(value)
    return value


def _error_details(
    error: Exception | None,
) -> tuple[ExitCode, dict[str, Any] | None]:
    if error is None:
        return ExitCode.SUCCESS, None
    if isinstance(error, CLIError):
        return error.exit_code, {
            "type": error.error_type,
            "phase": error.phase,
            "message": str(error),
            **error.details,
        }
    rule = next(rule for rule in _ERROR_RULES if isinstance(error, rule.exception_type))
    return rule.describe(error)


@click.group(cls=PluginGroup)
@click.option(
    "--config",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Connection config file (default: search for there.env).",
)
@click.option(
    "--timeout",
    type=click.FloatRange(min=0.0, min_open=True),
    help="Operation timeout in seconds.",
)
@click.option(
    "--max-output",
    default=DEFAULT_MAX_OUTPUT,
    show_default=True,
    type=click.IntRange(1, MAX_MAX_OUTPUT),
    callback=_set_max_output,
    help="Maximum retained bytes per output stream.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(("text", "json"), case_sensitive=False),
    default="text",
    show_default=True,
    callback=_set_output_format,
    help="Output format.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    is_eager=True,
    callback=_set_json_requested,
    help="Shortcut for --format json.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    config: Path | None,
    timeout: float | None,
    max_output: int,
    output_format: str,
    json_output: bool,
):
    """Run deterministic herethere commands."""
    ctx.obj = CLIContext(
        output_format="json" if json_output else output_format,
        config=config,
        timeout=timeout,
        max_output=max_output,
    )


def _validate_live_input(text: str, label: str) -> None:
    size = len(text.encode("utf-8"))
    if size > MAX_CODE_BYTES:
        raise click.UsageError(
            f"{label} is too large ({size} bytes > {MAX_CODE_BYTES} bytes)."
        )


def _read_utf8_stdin() -> str:
    try:
        stdin_buffer = getattr(sys.stdin, "buffer", None)
        if stdin_buffer is None:
            return sys.stdin.read()
        return stdin_buffer.read().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise LocalIOError(f"Could not read stdin as UTF-8: {exc}") from exc


def _load_text_source(
    file_path: str | None,
    inline_text: str | None,
    *,
    label: str,
    inline_option: str,
    byte_limit: int,
    missing_file_hint: str | None = None,
) -> str:
    """Load and validate one local-file, stdin, or inline text input."""
    if (file_path is None) == (inline_text is None):
        raise click.UsageError(
            f"Exactly one of FILE, '-', or {inline_option} is required."
        )

    local_input = inline_text is None
    if inline_text is not None:
        text = inline_text
    elif not file_path:
        raise click.UsageError(f"{label} must not be empty.")
    elif file_path == "-":
        text = _read_utf8_stdin()
    else:
        try:
            text = Path(file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            message = f"Could not read {file_path!r} as UTF-8: {exc}"
            if missing_file_hint is not None:
                message = f"{message}. {missing_file_hint}"
            raise LocalIOError(message) from exc

    if not text:
        raise click.UsageError(f"{label} must not be empty.")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        if local_input:
            raise LocalIOError(f"Could not read input as UTF-8: {exc}") from exc
        raise click.UsageError(f"{label} must be valid UTF-8.") from exc
    if size > byte_limit:
        raise click.UsageError(
            f"{label} is too large ({size} bytes > {byte_limit} bytes)."
        )
    return text


async def _run_remote_operation(
    config_path,
    timeout,
    operation,
    *,
    operation_phase="remote_execution",
):
    config = load_connection_config(config_path)
    client = Client()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout if timeout is not None else None

    async def wait_for_phase(awaitable, phase):
        remaining = None if deadline is None else deadline - loop.time()
        if remaining is not None and remaining <= 0:
            awaitable.close()
            raise OperationTimeout("operation timed out", phase=phase)
        try:
            if remaining is None:
                return await awaitable
            return await asyncio.wait_for(awaitable, timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise OperationTimeout("operation timed out", phase=phase) from exc

    try:
        try:
            await wait_for_phase(client.connect(config), "connection")
        except (asyncssh.PermissionDenied, OperationTimeout):
            raise
        except (asyncssh.Error, OSError) as exc:
            raise ConnectionFailure(str(exc)) from exc
        return await wait_for_phase(operation(client), operation_phase)
    finally:
        await client.disconnect()


def _call_remote(
    config,
    timeout,
    operation,
    *,
    operation_phase="remote_execution",
):
    try:
        return asyncio.run(
            _run_remote_operation(
                config,
                timeout,
                operation,
                operation_phase=operation_phase,
            )
        )
    except ProtocolVersionError as exc:
        raise ProtocolVersionFailure(str(exc), phase=operation_phase) from exc
    except ProtocolError as exc:
        raise RemoteOperationError(str(exc), phase=operation_phase) from exc
    except RemoteValueError as exc:
        raise RemoteExecutionFailure(
            RemoteError(
                remote_type=exc.error_type,
                message=exc.remote_message,
                traceback=exc.traceback,
            )
        ) from exc


@cli.command("ping")
@click.pass_context
def ping_command(ctx):
    """Check whether the remote herethere server is ready."""
    invocation = get_cli_context(ctx)

    async def operation(client):
        return await client.ping()

    response = _call_remote(
        invocation.config,
        (
            invocation.timeout
            if invocation.timeout is not None
            else DEFAULT_PING_TIMEOUT
        ),
        operation,
        operation_phase="ping",
    )
    if invocation.output_format == "text":
        click.echo(response)
        return None
    return {"response": response}


@cli.command("run")
@click.option(
    "-c",
    "--code",
    "code_text",
    help="Execute inline Python code.",
)
@click.argument("file_path", metavar="[FILE]", required=False)
@click.pass_context
def run_command(ctx, code_text, file_path):
    """Execute a local FILE, inline code, or stdin in the live namespace."""
    invocation = get_cli_context(ctx)
    code = _load_text_source(
        file_path,
        code_text,
        label="Python code",
        inline_option="--code/-c",
        byte_limit=MAX_CODE_BYTES,
    )

    stdout = sys.stdout
    stderr = sys.stderr
    if invocation.output_format == "text":
        stdout, stderr = _text_streams(ctx)

    async def operation(client):
        return await client.execute(code, stdout=stdout, stderr=stderr)

    result = _call_remote(invocation.config, invocation.timeout, operation)
    if not result.ok:
        raise RemoteExecutionFailure(result.error)


def _strict_json_value(value):
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueSerializationFailure(
            f"Remote value is not JSON serializable: {exc}"
        ) from exc


@cli.command("get")
@click.argument("expression")
@click.pass_context
def get_command(ctx, expression):
    """Get one Python expression value from the live namespace."""
    invocation = get_cli_context(ctx)
    try:
        ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise click.UsageError(
            "EXPRESSION must be exactly one Python expression."
        ) from exc
    _validate_live_input(expression, "Expression")

    async def operation(client):
        return await client.get(expression)

    value = _call_remote(invocation.config, invocation.timeout, operation)
    if invocation.output_format == "text":
        stdout, _ = _text_streams(ctx)
        click.echo(repr(value), file=stdout)
        return None
    return {"value": _strict_json_value(value)}


@cli.command("logs")
@click.option(
    "--records",
    type=click.IntRange(1, DEFAULT_MAX_LOG_RECORDS),
    help="Return at most this many newest log records.",
)
@click.pass_context
def logs_command(ctx, records):
    """Return a finite snapshot of recent remote Python logs."""
    invocation = get_cli_context(ctx)

    async def operation(client):
        return await client.logs(max_records=records)

    snapshot = _call_remote(
        invocation.config,
        invocation.timeout,
        operation,
        operation_phase="log_retrieval",
    )
    collector = BoundedTextCollector(invocation.max_output)
    collector.write(snapshot.text)
    text = collector.getvalue()
    truncated = snapshot.truncated or collector.truncated

    if invocation.output_format == "text":
        click.echo(text, nl=False)
        return None
    return {
        "text": text,
        "bytes": snapshot.bytes,
        "records": snapshot.records,
        "truncated": truncated,
        "server_truncated": snapshot.truncated,
    }


@cli.command("shell")
@click.option(
    "-c",
    "--command",
    "command_text",
    help="Execute an inline shell command.",
)
@click.argument("file_path", metavar="[FILE]", required=False)
@click.pass_context
def shell_command(ctx, command_text, file_path):
    """Execute a local script using the remote platform shell.

    Use --command/-c for inline shell code, or '-' for local stdin.
    To execute a script already on the remote host, use -c './script.sh'.
    """
    invocation = get_cli_context(ctx)
    command = _load_text_source(
        file_path,
        command_text,
        label="Shell input",
        inline_option="--command/-c",
        byte_limit=MAX_SHELL_COMMAND_BYTES,
        missing_file_hint="Inline shell commands require --command/-c.",
    )
    stdout = sys.stdout
    stderr = sys.stderr
    if invocation.output_format == "text":
        stdout, stderr = _text_streams(ctx)

    async def operation(client):
        return await client.execute_shell(
            command,
            stdout=stdout,
            stderr=stderr,
        )

    result = _call_remote(
        invocation.config,
        invocation.timeout,
        operation,
        operation_phase="shell_execution",
    )
    if not result.ok:
        raise RemoteShellFailure(result.returncode)
    return {"returncode": result.returncode}


def _split_transfer_paths(paths, default_destination):
    """Split transfer arguments into sources and their final destination."""
    if len(paths) == 1:
        return list(paths), default_destination
    return list(paths[:-1]), paths[-1]


def _validate_upload_sources(local_paths):
    """Reject missing upload sources before opening a remote connection."""
    for local_path in local_paths:
        if not Path(local_path).exists():
            raise LocalIOError(f"Local upload source does not exist: {local_path!r}")


def _raise_transfer_error(error):
    """Map transfer failures onto the stable CLI error model."""
    if isinstance(
        error,
        (
            asyncssh.DisconnectError,
            asyncssh.SFTPConnectionLost,
        ),
    ):
        raise ConnectionFailure(str(error)) from error
    if isinstance(error, asyncssh.Error):
        raise SFTPFailure(str(error)) from error
    raise LocalIOError(str(error)) from error


@cli.command("upload")
@click.argument(
    "paths",
    nargs=-1,
    required=True,
    metavar="LOCAL_PATH... [REMOTE_PATH]",
)
@click.pass_context
def upload_command(ctx, paths):
    """Upload files and directories recursively over SFTP.

    With one path, upload to the current remote SFTP directory. With multiple
    paths, the last path is the remote destination.
    """
    invocation = get_cli_context(ctx)
    local_paths, remote_path = _split_transfer_paths(paths, ".")
    _validate_upload_sources(local_paths)

    async def operation(client):
        try:
            await client.upload(local_paths, remote_path)
        except (asyncssh.Error, OSError, UnicodeError) as exc:
            _raise_transfer_error(exc)

    _call_remote(
        invocation.config,
        invocation.timeout,
        operation,
        operation_phase="transfer",
    )
    return {
        "local_paths": local_paths,
        "remote_path": remote_path,
    }


@cli.command("download")
@click.argument(
    "paths",
    nargs=-1,
    required=True,
    metavar="REMOTE_PATH... [LOCAL_PATH]",
)
@click.pass_context
def download_command(ctx, paths):
    """Download files and directories recursively over SFTP.

    With one path, download to the current local directory. With multiple
    paths, the last path is the local destination.
    """
    invocation = get_cli_context(ctx)
    remote_paths, local_path = _split_transfer_paths(paths, ".")

    async def operation(client):
        try:
            await client.download(remote_paths, local_path)
        except (asyncssh.Error, OSError, UnicodeError) as exc:
            _raise_transfer_error(exc)

    _call_remote(
        invocation.config,
        invocation.timeout,
        operation,
        operation_phase="transfer",
    )
    return {
        "remote_paths": remote_paths,
        "local_path": local_path,
    }


__all__ = (
    "BoundedTextCollector",
    "CLIContext",
    "CLIError",
    "ConnectionFailure",
    "DEFAULT_MAX_OUTPUT",
    "ExitCode",
    "LocalIOError",
    "MAX_MAX_OUTPUT",
    "PluginError",
    "RemoteOperationError",
    "RemoteShellFailure",
    "SFTPFailure",
    "OperationTimeout",
    "ProtocolVersionFailure",
    "RemoteExecutionFailure",
    "ValueSerializationFailure",
    "cli",
    "get_command",
    "get_cli_context",
    "logs_command",
    "load_connection_config",
    "run_command",
    "shell_command",
    "download_command",
    "upload_command",
)
