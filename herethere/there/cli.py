"""Standalone command-line interface for herethere."""

import io
import json
import sys
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from enum import IntEnum
from importlib import metadata
from pathlib import Path
from typing import Any

import asyncssh
import click

from herethere.everywhere.config import ConnectionConfig, ConnectionConfigError

DEFAULT_MAX_OUTPUT = 64 * 1024
MAX_MAX_OUTPUT = 1024 * 1024
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

    def __init__(self, message: str, *, phase: str | None = None):
        super().__init__(message)
        if phase is not None:
            self.phase = phase


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


def remote_options(function):
    """Add the options shared by commands which contact a remote target."""

    options = (
        click.option(
            "--config",
            type=click.Path(path_type=Path, dir_okay=False),
            help="Connection config file (default: search for there.env).",
        ),
        click.option(
            "--timeout",
            type=click.FloatRange(min=0.0, min_open=True),
            help="Operation timeout in seconds.",
        ),
        click.option(
            "--max-output",
            default=DEFAULT_MAX_OUTPUT,
            show_default=True,
            type=click.IntRange(1, MAX_MAX_OUTPUT),
            callback=_set_max_output,
            help="Maximum retained bytes per output stream.",
        ),
    )
    for option in reversed(options):
        function = option(function)
    return function


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
        return captured


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
        }
    rule = next(rule for rule in _ERROR_RULES if isinstance(error, rule.exception_type))
    return rule.describe(error)


@click.group(cls=PluginGroup)
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
def cli(ctx: click.Context, output_format: str, json_output: bool):
    """Run deterministic herethere commands."""
    ctx.obj = CLIContext(output_format="json" if json_output else output_format)


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
    "cli",
    "load_connection_config",
    "remote_options",
)
