import asyncio
import builtins
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import asyncssh
import click
import pytest
from click.testing import CliRunner

from herethere.everywhere.config import ConnectionConfig, ConnectionConfigError
from herethere.everywhere.recent_logs import RecentLogsSnapshot
from herethere.everywhere.shell import ShellResult
from herethere.there import cli as cli_module
from herethere.there.cli import (
    DEFAULT_PING_TIMEOUT,
    MAX_MAX_OUTPUT,
    BoundedTextCollector,
    CLIContext,
    ConnectionFailure,
    ExitCode,
    LocalIOError,
    OperationTimeout,
    PluginError,
    ProtocolVersionFailure,
    RemoteOperationError,
    cli,
    get_cli_context,
    load_connection_config,
)


class EntryPointStub:
    def __init__(self, name, value=None, error=None):
        self.name = name
        self.value = value
        self.error = error
        self.loaded = False

    def load(self):
        self.loaded = True
        if self.error:
            raise self.error
        return self.value


def invoke_json(args):
    result = CliRunner().invoke(cli, ["--json", *args])
    return result, json.loads(result.output)


def install_fake_remote(
    monkeypatch,
    *,
    ping=None,
    execute=None,
    execute_worker=None,
    get=None,
    get_worker=None,
    upload=None,
    download=None,
    logs=None,
    execute_shell=None,
):
    class FakeClient:
        async def ping(self):
            if ping is not None:
                return ping()
            return "pong"

        async def execute(self, code, stdout=None, stderr=None):
            if execute is not None:
                return execute(code, stdout, stderr)
            return SimpleNamespace(ok=True, error=None)

        async def execute_worker(self, code, stdout=None, stderr=None):
            if execute_worker is not None:
                return execute_worker(code, stdout, stderr)
            return SimpleNamespace(ok=True, error=None)

        async def get(self, expression):
            if get is not None:
                return get(expression)
            return 42

        async def get_worker(self, expression):
            if get_worker is not None:
                return get_worker(expression)
            return 42

        async def upload(self, local_paths, remote_path):
            if upload is not None:
                return upload(local_paths, remote_path)
            return None

        async def download(self, remote_paths, local_path):
            if download is not None:
                return download(remote_paths, local_path)
            return None

        async def logs(self, max_records=None):
            if logs is not None:
                return logs(max_records)
            return RecentLogsSnapshot(
                text="",
                bytes=0,
                records=0,
                truncated=False,
            )

        async def execute_shell(self, command, stdout=None, stderr=None):
            if execute_shell is not None:
                return execute_shell(command, stdout, stderr)
            return ShellResult(returncode=0)

    def call(config, timeout, operation, **kwargs):
        del config, timeout
        assert kwargs.get("operation_phase", "remote_execution") in {
            "log_retrieval",
            "ping",
            "remote_execution",
            "shell_execution",
            "transfer",
        }
        return asyncio.run(operation(FakeClient()))

    monkeypatch.setattr(cli_module, "_call_remote", call)


def test_ping_text_prints_exact_response(monkeypatch):
    received = {}

    def call(config, timeout, operation, **kwargs):
        del config, operation, kwargs
        received["timeout"] = timeout
        return "pong"

    monkeypatch.setattr(cli_module, "_call_remote", call)

    result = CliRunner().invoke(cli, ["ping"])

    assert result.exit_code == ExitCode.SUCCESS
    assert result.output == "pong\n"
    assert result.return_value is None
    assert received["timeout"] == DEFAULT_PING_TIMEOUT


def test_ping_help_only_shows_operation_options():
    result = CliRunner().invoke(cli, ["ping", "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "--timeout" not in result.output
    assert "--config" not in result.output
    assert "--max-output" not in result.output


def test_ping_json_returns_response_outside_captured_stdout(monkeypatch):
    install_fake_remote(monkeypatch)

    result, payload = invoke_json(["ping"])

    assert result.exit_code == ExitCode.SUCCESS
    assert payload["response"] == "pong"
    assert payload["stdout"] == ""
    assert payload["stdout_bytes"] == 0
    assert payload["stderr"] == ""


def test_ping_protocol_error_uses_ping_phase(monkeypatch):
    def fail(awaitable):
        awaitable.close()
        raise cli_module.ProtocolError("invalid ping")

    monkeypatch.setattr(cli_module.asyncio, "run", fail)

    result, payload = invoke_json(["ping"])

    assert result.exit_code == ExitCode.REMOTE
    assert payload["error"]["phase"] == "ping"


def test_ping_timeout_uses_ping_phase(monkeypatch):
    class ClientStub:
        async def connect(self, config):
            del config

        async def ping(self):
            await asyncio.sleep(1)

        async def disconnect(self):
            pass

    monkeypatch.setattr(cli_module, "Client", ClientStub)
    monkeypatch.setattr(cli_module, "load_connection_config", lambda config: object())

    result, payload = invoke_json(["--timeout", "0.1", "ping"])

    assert result.exit_code == ExitCode.TIMEOUT
    assert payload["error"]["phase"] == "ping"


def test_console_help_lists_builtins_without_ipython(monkeypatch):
    original_import = builtins.__import__

    def reject_ipython(name, *args, **kwargs):
        if name.startswith(("IPython", "ipywidgets")):
            raise AssertionError(f"unexpected optional import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_ipython)
    sys.modules.pop("herethere.there.cli", None)
    module = importlib.import_module("herethere.there.cli")

    result = CliRunner().invoke(module.cli, ["--help"])

    assert result.exit_code == 0
    for option in (
        "--config",
        "--timeout",
        "--max-output",
        "--format [text|json]",
        "--json",
    ):
        assert option in result.output
    assert "there_group" not in result.output


def test_console_script_is_registered():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert '[project.scripts]\nthere = "herethere.there.cli:cli"' in pyproject


@pytest.mark.parametrize("option", [["--json"], ["--format", "json"]])
def test_json_unknown_command_is_one_object(option):
    result = CliRunner().invoke(cli, [*option, "missing"])
    payload = json.loads(result.output)

    assert result.exit_code == ExitCode.USAGE
    assert result.output.count("\n") == 1
    assert payload["ok"] is False
    assert payload["command"] == "missing"
    assert payload["error"]["type"] == "UsageError"


def test_json_and_explicit_text_conflict():
    result = CliRunner().invoke(cli, ["--json", "--format", "text", "missing"])
    payload = json.loads(result.output)

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["message"] == "--json cannot be used with --format text."


def test_json_and_explicit_json_are_allowed():
    result = CliRunner().invoke(cli, ["--json", "--format", "json", "missing"])
    assert result.exit_code == ExitCode.USAGE
    assert json.loads(result.output)["error"]["type"] == "UsageError"


def test_json_selection_precedes_invalid_format_parsing():
    result = CliRunner().invoke(cli, ["--json", "--format", "yaml", "missing"])
    assert result.exit_code == ExitCode.USAGE
    assert json.loads(result.output)["error"]["type"] == "UsageError"


def test_root_option_after_command_is_not_accepted(monkeypatch):
    command = click.Command("external")
    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("external", command),)
    )

    result = CliRunner().invoke(cli, ["external", "--json"])
    assert result.exit_code == ExitCode.USAGE
    assert not result.output.startswith("{")


@pytest.mark.parametrize(
    "trailing_option",
    (
        ["--config", "custom.env"],
        ["--timeout", "1"],
        ["--max-output", "12"],
    ),
)
def test_connection_root_options_after_builtin_are_rejected(trailing_option):
    result, payload = invoke_json(["run", *trailing_option, "-c", "pass"])

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["type"] == "UsageError"


def test_help_is_never_json_wrapped():
    result = CliRunner().invoke(cli, ["--json", "--help"])
    assert result.exit_code == 0
    assert result.output.startswith("Usage:")


def test_json_success_captures_output_and_result_fields(monkeypatch):
    @click.command()
    def extension():
        click.echo("out")
        click.echo("err", err=True)
        return {"answer": 42, "ok": False}

    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("extension", extension),)
    )
    result, payload = invoke_json(["extension"])

    assert result.exit_code == 0
    assert payload == {
        "ok": True,
        "command": "extension",
        "exit_code": 0,
        "stdout": "out\n",
        "stdout_bytes": 4,
        "stdout_truncated": False,
        "stderr": "err\n",
        "stderr_bytes": 4,
        "stderr_truncated": False,
        "error": None,
        "answer": 42,
    }


@pytest.mark.parametrize(
    ("exception", "exit_code", "error_type"),
    [
        (TimeoutError("late"), ExitCode.TIMEOUT, "TimeoutError"),
        (TimeoutError(), ExitCode.TIMEOUT, "TimeoutError"),
        (
            asyncssh.PermissionDenied("denied"),
            ExitCode.CONNECTION,
            "AuthenticationError",
        ),
        (
            asyncssh.ConnectionLost("lost"),
            ExitCode.CONNECTION,
            "ConnectionError",
        ),
        (ConnectionFailure("denied"), ExitCode.CONNECTION, "ConnectionError"),
        (RemoteOperationError("failed"), ExitCode.REMOTE, "RemoteOperationError"),
        (LocalIOError("missing"), ExitCode.LOCAL_IO, "LocalIOError"),
        (click.ClickException("bad"), ExitCode.REMOTE, "ClickException"),
        (RuntimeError("bug"), ExitCode.REMOTE, "InternalError"),
        (
            ConnectionConfigError("not configured"),
            ExitCode.USAGE,
            "ConfigError",
        ),
    ],
)
def test_json_error_mapping(monkeypatch, exception, exit_code, error_type):
    @click.command()
    def failure():
        raise exception

    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("failure", failure),)
    )
    result, payload = invoke_json(["failure"])

    assert result.exit_code == exit_code
    assert payload["error"]["type"] == error_type
    assert payload["stdout"] == ""
    assert payload["stderr"] == ""


def test_bounded_collector_retains_byte_tail_and_metadata():
    collector = BoundedTextCollector(4)
    assert collector.encoding == "utf-8"
    assert collector.writable()
    assert collector.write("ab") == 2
    collector.write_bytes(b"cdef")

    assert collector.getvalue() == "cdef"
    assert collector.metadata("stdout") == {
        "stdout": "cdef",
        "stdout_bytes": 6,
        "stdout_truncated": True,
    }


def test_bounded_collector_discards_old_bytes_for_small_writes():
    collector = BoundedTextCollector(4)
    collector.write("abc")
    collector.write("def")
    assert collector.getvalue() == "cdef"


def test_bounded_collector_limit_can_be_reduced():
    collector = BoundedTextCollector(10)
    collector.write("abcdef")
    collector.set_limit(3)
    assert collector.getvalue() == "def"
    with pytest.raises(ValueError, match="range"):
        collector.set_limit(0)


def test_bounded_collector_decodes_truncated_utf8_with_replacement():
    collector = BoundedTextCollector(2)
    collector.write("x€")
    assert collector.getvalue() == "��"
    assert collector.byte_count == 4
    assert collector.truncated


@pytest.mark.parametrize("limit", [0, MAX_MAX_OUTPUT + 1])
def test_bounded_collector_validates_limit(limit):
    with pytest.raises(ValueError, match="range"):
        BoundedTextCollector(limit)


def test_bounded_collector_requires_text():
    with pytest.raises(TypeError, match="must be str"):
        BoundedTextCollector().write(b"bytes")


@pytest.mark.parametrize("value", ["bad", "0", str(MAX_MAX_OUTPUT + 1)])
def test_max_output_option_validates_range(monkeypatch, value):
    @click.command()
    def remote():
        pass

    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("remote", remote),)
    )
    result, payload = invoke_json(["--max-output", value, "remote"])
    assert result.exit_code == ExitCode.USAGE
    assert "--max-output" in payload["error"]["message"]


def test_max_output_equals_form_bounds_captured_plugin_output(monkeypatch):
    @click.command()
    def noisy():
        click.echo("abcdefghij", nl=False)

    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("noisy", noisy),)
    )
    result, payload = invoke_json(["--max-output=4", "noisy"])

    assert result.exit_code == 0
    assert payload["stdout"] == "ghij"
    assert payload["stdout_bytes"] == 10
    assert payload["stdout_truncated"] is True


def test_root_max_output_bounds_command_error_output(monkeypatch):
    @click.command()
    def noisy():
        click.echo("abcdefghij", nl=False)
        raise click.UsageError("bad command")

    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("noisy", noisy),)
    )

    result, payload = invoke_json(["--max-output", "4", "noisy"])

    assert result.exit_code == ExitCode.USAGE
    assert payload["stdout"] == "ghij"
    assert payload["stdout_bytes"] == 10
    assert payload["stdout_truncated"] is True


def test_root_max_output_bounds_unknown_command_discovery_output(monkeypatch):
    def discover():
        click.echo("abcdefghij", nl=False)
        return ()

    monkeypatch.setattr(cli_module, "_entry_points", discover)

    result, payload = invoke_json(["--max-output", "4", "missing"])

    assert result.exit_code == ExitCode.USAGE
    assert payload["stdout"] == "ghij"
    assert payload["stdout_bytes"] == 10
    assert payload["stdout_truncated"] is True


def test_root_max_output_bounds_plugin_load_output(monkeypatch):
    class NoisyEntryPoint(EntryPointStub):
        def load(self):
            click.echo("abcdefghij", nl=False)
            raise RuntimeError("broken")

    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (NoisyEntryPoint("broken"),)
    )

    result, payload = invoke_json(["--max-output", "4", "broken"])

    assert result.exit_code == ExitCode.USAGE
    assert payload["stdout"] == "ghij"
    assert payload["stdout_bytes"] == 10
    assert payload["stdout_truncated"] is True


def test_format_equals_form_is_parsed_by_click():
    result = CliRunner().invoke(cli, ["--format=json", "missing"])
    assert result.exit_code == ExitCode.USAGE
    assert json.loads(result.output)["error"]["type"] == "UsageError"


def test_root_invocation_options_parse_consistently(monkeypatch):
    received = {}

    @click.command()
    @click.pass_context
    def command(ctx):
        invocation = get_cli_context(ctx)
        received.update(
            config=invocation.config,
            timeout=invocation.timeout,
            max_output=invocation.max_output,
        )

    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("command", command),)
    )
    result = CliRunner().invoke(
        cli,
        [
            "--config",
            "custom.env",
            "--timeout",
            "1.5",
            "--max-output",
            "12",
            "command",
        ],
    )

    assert result.exit_code == 0
    assert received == {
        "config": Path("custom.env"),
        "timeout": 1.5,
        "max_output": 12,
    }


def test_get_cli_context_rejects_uninitialized_root():
    with click.Context(click.Command("command")) as ctx:
        with pytest.raises(TypeError, match="not initialized"):
            get_cli_context(ctx)


def test_root_invocation_defaults_are_typed(monkeypatch):
    received = {}

    @click.command()
    @click.pass_context
    def command(ctx):
        received["invocation"] = get_cli_context(ctx)

    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("command", command),)
    )

    result = CliRunner().invoke(cli, ["command"])

    assert result.exit_code == ExitCode.SUCCESS
    assert received["invocation"] == CLIContext()


def test_root_config_is_lazy_for_plugin_commands(monkeypatch):
    @click.command()
    @click.pass_context
    def command(ctx):
        assert get_cli_context(ctx).config == Path("missing.env")

    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("command", command),)
    )
    monkeypatch.setattr(
        cli_module,
        "load_connection_config",
        lambda config: pytest.fail(f"loaded config unexpectedly: {config}"),
    )

    result = CliRunner().invoke(cli, ["--config", "missing.env", "command"])

    assert result.exit_code == ExitCode.SUCCESS


def test_root_connection_options_reach_every_builtin(tmp_path, monkeypatch):
    upload_source = tmp_path / "upload.txt"
    upload_source.write_text("content", encoding="utf-8")
    cases = (
        ("ping", [], "pong"),
        ("run", ["-c", "pass"], SimpleNamespace(ok=True, error=None)),
        ("get", ["1"], 1),
        (
            "logs",
            [],
            RecentLogsSnapshot(text="", bytes=0, records=0, truncated=False),
        ),
        ("shell", ["-c", "true"], ShellResult(returncode=0)),
        ("upload", [str(upload_source)], None),
        ("download", ["remote.txt"], None),
    )

    for command, arguments, return_value in cases:
        received = {}

        def call(
            config,
            timeout,
            operation,
            _received=received,
            _return_value=return_value,
            **kwargs,
        ):
            del operation, kwargs
            _received.update(config=config, timeout=timeout)
            return _return_value

        monkeypatch.setattr(cli_module, "_call_remote", call)
        result = CliRunner().invoke(
            cli,
            [
                "--config",
                "selected.env",
                "--timeout",
                "3.5",
                command,
                *arguments,
            ],
        )

        assert result.exit_code == ExitCode.SUCCESS
        assert received == {"config": Path("selected.env"), "timeout": 3.5}


def test_explicit_config_and_environment_override(tmp_path, monkeypatch):
    path = tmp_path / "custom.env"
    path.write_text(
        "THERE_HOST=file-host\n"
        "THERE_PORT=9000\n"
        "THERE_USERNAME=file-user\n"
        "THERE_PASSWORD=file-password\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("THERE_HOST", "environment-host")

    config = load_connection_config(path)

    assert config == ConnectionConfig(
        host="environment-host",
        port=9000,
        username="file-user",
        password="file-password",
    )


def test_default_config_searches_parent_directories(tmp_path, monkeypatch):
    path = tmp_path / "there.env"
    path.write_text(
        "THERE_USERNAME=user\nTHERE_PASSWORD=password\n",
        encoding="utf-8",
    )
    child = tmp_path / "one" / "two"
    child.mkdir(parents=True)
    monkeypatch.chdir(child)
    monkeypatch.delenv("THERE_USERNAME", raising=False)
    monkeypatch.delenv("THERE_PASSWORD", raising=False)

    assert load_connection_config() == ConnectionConfig(
        host="127.0.0.1",
        port=8022,
        username="user",
        password="password",
    )


def test_plugin_help_does_not_load_plugin(monkeypatch):
    entry_point = EntryPointStub("external", click.Command("external"))
    monkeypatch.setattr(cli_module, "_entry_points", lambda: (entry_point,))

    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "external" in result.output
    assert "[plugin]" in result.output
    assert entry_point.loaded is False


def test_help_omits_hidden_commands_and_plugin_collision(monkeypatch):
    group = cli_module.PluginGroup("root")
    group.add_command(click.Command("hidden", hidden=True))
    group.add_command(click.Command("visible"))
    entry_point = EntryPointStub("visible", click.Command("replacement"))
    monkeypatch.setattr(cli_module, "_entry_points", lambda: (entry_point,))

    result = CliRunner().invoke(group, ["--help"])

    assert result.exit_code == 0
    assert "hidden" not in result.output
    assert result.output.count("visible") == 1
    assert entry_point.loaded is False


def test_empty_group_help_has_no_commands_section(monkeypatch):
    group = cli_module.PluginGroup("root")
    monkeypatch.setattr(cli_module, "_entry_points", lambda: ())

    result = CliRunner().invoke(group, ["--help"])

    assert result.exit_code == 0
    assert "Commands:" not in result.output


def test_plugin_is_loaded_only_when_selected(monkeypatch):
    entry_point = EntryPointStub("external", click.Command("external"))
    monkeypatch.setattr(cli_module, "_entry_points", lambda: (entry_point,))

    result = CliRunner().invoke(cli, ["external"])

    assert result.exit_code == 0
    assert entry_point.loaded is True


def test_builtin_wins_plugin_collision(monkeypatch):
    group = cli_module.PluginGroup("root")
    group.add_command(click.Command("run"))
    entry_point = EntryPointStub("run", click.Command("replacement"))
    monkeypatch.setattr(cli_module, "_entry_points", lambda: (entry_point,))

    result = CliRunner().invoke(group, ["run"])

    assert result.exit_code == 0
    assert entry_point.loaded is False


def test_duplicate_plugin_names_are_structured_error(monkeypatch):
    monkeypatch.setattr(
        cli_module,
        "_entry_points",
        lambda: (EntryPointStub("same"), EntryPointStub("same")),
    )
    result, payload = invoke_json(["same"])

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["type"] == "PluginError"
    assert "Duplicate" in payload["error"]["message"]


@pytest.mark.parametrize(
    ("entry_point", "message"),
    [
        (
            EntryPointStub("broken", error=RuntimeError("import failed")),
            "Could not load",
        ),
        (EntryPointStub("broken", value=object()), "click.Command"),
    ],
)
def test_plugin_load_failures_are_structured(monkeypatch, entry_point, message):
    monkeypatch.setattr(cli_module, "_entry_points", lambda: (entry_point,))
    result, payload = invoke_json(["broken"])

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["type"] == "PluginError"
    assert message in payload["error"]["message"]


def test_plugin_discovery_failure_is_structured(monkeypatch):
    def fail():
        raise PluginError("discovery failed")

    monkeypatch.setattr(cli_module, "_entry_points", fail)
    result, payload = invoke_json(["external"])

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["phase"] == "plugin"


def test_importlib_plugin_discovery_failure(monkeypatch):
    def fail(**kwargs):
        del kwargs
        raise RuntimeError("metadata failed")

    monkeypatch.setattr(cli_module.metadata, "entry_points", fail)
    with pytest.raises(PluginError, match="metadata failed"):
        cli_module._entry_points()


def test_importlib_compatibility_plugin_discovery(monkeypatch):
    class Selectable:
        def select(self, **kwargs):
            assert kwargs == {"group": "herethere.cli"}
            return ("entry",)

    calls = 0

    def compatible(**kwargs):
        nonlocal calls
        calls += 1
        if kwargs:
            raise TypeError
        return Selectable()

    monkeypatch.setattr(cli_module.metadata, "entry_points", compatible)
    assert cli_module._entry_points() == ("entry",)
    assert calls == 2


def test_group_list_commands_includes_plugins(monkeypatch):
    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("external"),)
    )
    context = click.Context(cli)
    assert "external" in cli.list_commands(context)


def test_cli_main_non_standalone_returns_expected_exit_code(monkeypatch, capsys):
    @click.command()
    def failure():
        raise ConnectionFailure("denied", phase="authentication")

    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("failure", failure),)
    )
    result = cli.main(["failure"], standalone_mode=False)

    assert result == ExitCode.CONNECTION
    assert capsys.readouterr().err == "Error: denied\n"


def test_cli_main_standalone_discards_successful_command_result(monkeypatch, capsys):
    @click.command()
    def successful():
        return {"uploaded": True}

    monkeypatch.setattr(
        cli_module,
        "_entry_points",
        lambda: (EntryPointStub("successful", successful),),
    )

    result = cli.main(["successful"])

    assert result is None
    assert capsys.readouterr().out == ""


def test_json_main_non_standalone_returns_exit_code(capsys):
    result = cli.main(["--json", "missing"], standalone_mode=False)
    payload = json.loads(capsys.readouterr().out)

    assert result == ExitCode.USAGE
    assert payload["command"] == "missing"


def test_text_mode_propagates_unexpected_errors(monkeypatch):
    @click.command()
    def failure():
        raise RuntimeError("bug")

    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("failure", failure),)
    )
    result = CliRunner().invoke(cli, ["failure"])

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)


def test_option_callbacks_ignore_non_cli_groups():
    command = click.Command("plain")
    context = click.Context(command)

    assert cli_module._set_output_format(context, None, "text") == "text"
    assert cli_module._set_json_requested(context, None, True) is True
    assert cli_module._set_max_output(context, None, 10) == 10


def test_max_output_callback_skips_missing_collector():
    group = cli_module.PluginGroup("root")
    collector = BoundedTextCollector()
    group._stdout_collector = collector
    group._stderr_collector = None
    context = click.Context(group)

    assert cli_module._set_max_output(context, None, 10) == 10
    assert collector.limit == 10


def test_json_without_command_has_empty_command_name():
    result = CliRunner().invoke(cli, ["--json"])
    payload = json.loads(result.output)
    assert result.exit_code == ExitCode.USAGE
    assert payload["command"] == ""


@pytest.mark.parametrize(
    ("args", "input_text", "expected"),
    [
        (["run", "--code", "print(1)"], None, "print(1)"),
        (["run", "-c", "print(2)"], None, "print(2)"),
        (["run", "-"], "print(3)", "print(3)"),
    ],
)
def test_run_accepts_code_and_stdin(monkeypatch, args, input_text, expected):
    received = {}

    def execute(code, stdout, stderr):
        received.update(code=code, stdout=stdout, stderr=stderr)
        return SimpleNamespace(ok=True, error=None)

    install_fake_remote(monkeypatch, execute=execute)
    result = CliRunner().invoke(cli, args, input=input_text)

    assert result.exit_code == 0
    assert received["code"] == expected
    assert received["stdout"] is not None
    assert received["stderr"] is not None
    assert result.return_value is None


def test_run_accepts_utf8_file(monkeypatch, tmp_path):
    source = tmp_path / "app.py"
    source.write_text("message = '€'", encoding="utf-8")
    received = {}

    def execute(code, stdout, stderr):
        del stdout, stderr
        received["code"] = code
        return SimpleNamespace(ok=True, error=None)

    install_fake_remote(monkeypatch, execute=execute)
    result = CliRunner().invoke(cli, ["run", str(source)])

    assert result.exit_code == 0
    assert received["code"] == "message = '€'"


@pytest.mark.parametrize(
    "args",
    (
        ["run"],
        ["run", "app.py", "--code", "pass"],
    ),
)
def test_run_rejects_missing_or_conflicting_sources(args):
    result, payload = invoke_json(args)

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["type"] == "UsageError"
    assert "Exactly one" in payload["error"]["message"]


@pytest.mark.parametrize("args", (["run", "--code", ""], ["run", ""]))
def test_run_rejects_empty_sources(args):
    result, payload = invoke_json(args)

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["type"] == "UsageError"
    assert "must not be empty" in payload["error"]["message"]


def test_run_maps_local_file_error():
    result, payload = invoke_json(["run", "missing-app.py"])

    assert result.exit_code == ExitCode.LOCAL_IO
    assert payload["error"]["type"] == "LocalIOError"


@pytest.mark.parametrize("command", ("run", "shell"))
def test_commands_reject_empty_files(command, tmp_path):
    source = tmp_path / "empty"
    source.write_text("", encoding="utf-8")

    result, payload = invoke_json([command, str(source)])

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["type"] == "UsageError"
    assert "must not be empty" in payload["error"]["message"]


@pytest.mark.parametrize("command", ("run", "shell"))
def test_commands_reject_empty_stdin(command):
    result = CliRunner().invoke(cli, ["--json", command, "-"], input="")
    payload = json.loads(result.output)

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["type"] == "UsageError"
    assert "must not be empty" in payload["error"]["message"]


@pytest.mark.parametrize("command", ("run", "shell"))
def test_commands_reject_invalid_utf8_files(command, tmp_path):
    source = tmp_path / "invalid"
    source.write_bytes(b"\xff")

    result, payload = invoke_json([command, str(source)])

    assert result.exit_code == ExitCode.LOCAL_IO
    assert payload["error"]["type"] == "LocalIOError"
    assert "UTF-8" in payload["error"]["message"]


@pytest.mark.parametrize("command", ("run", "shell"))
def test_commands_reject_invalid_utf8_stdin(command):
    result = CliRunner().invoke(cli, ["--json", command, "-"], input=b"\xff")
    payload = json.loads(result.output)

    assert result.exit_code == ExitCode.LOCAL_IO
    assert payload["error"]["type"] == "LocalIOError"
    assert "UTF-8" in payload["error"]["message"]


@pytest.mark.parametrize("command", ("run", "shell"))
def test_commands_reject_extra_positional_arguments(command):
    result, payload = invoke_json([command, "one", "two"])

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["type"] == "UsageError"


def test_run_maps_stdin_read_error(monkeypatch):
    class BrokenInput:
        def read(self):
            raise OSError("broken input")

    monkeypatch.setattr(cli_module.sys, "stdin", BrokenInput())

    with pytest.raises(LocalIOError, match="broken input"):
        cli_module._load_text_source(
            "-",
            None,
            label="Python code",
            inline_option="--code/-c",
        )


@pytest.mark.parametrize("mode", ("inline", "file", "stdin"))
def test_shell_rejects_oversized_input_before_connecting(monkeypatch, tmp_path, mode):
    oversized = "€" * 21846
    source = tmp_path / f"shell-{mode}"
    input_text = None
    if mode == "inline":
        args = ["shell", "--command", oversized]
    elif mode == "file":
        source.write_text(oversized, encoding="utf-8")
        args = ["shell", str(source)]
    else:
        args = ["shell", "-"]
        input_text = oversized

    def fail(*args, **kwargs):
        del args, kwargs
        pytest.fail("remote operation called")

    monkeypatch.setattr(cli_module, "_call_remote", fail)
    result = CliRunner().invoke(cli, ["--json", *args], input=input_text)
    payload = json.loads(result.output)

    assert result.exit_code == ExitCode.USAGE
    assert "too large" in payload["error"]["message"]


@pytest.mark.parametrize("mode", ("inline", "file", "stdin"))
def test_run_accepts_input_larger_than_64_kib(monkeypatch, tmp_path, mode):
    code = "#" * (64 * 1024) + "\nlarge_input_complete = True"
    source = tmp_path / f"run-{mode}.py"
    input_text = None
    if mode == "inline":
        args = ["run", "--code", code]
    elif mode == "file":
        source.write_text(code, encoding="utf-8")
        args = ["run", str(source)]
    else:
        args = ["run", "-"]
        input_text = code

    received = {}

    def execute(remote_code, stdout, stderr):
        del stdout, stderr
        received["code"] = remote_code
        return SimpleNamespace(ok=True, error=None)

    install_fake_remote(monkeypatch, execute=execute)
    result = CliRunner().invoke(cli, ["--json", *args], input=input_text)

    assert result.exit_code == ExitCode.SUCCESS
    assert received["code"] == code


def test_run_json_captures_output_and_remote_exception(monkeypatch):
    remote_error = SimpleNamespace(
        remote_type="ValueError",
        message="bad",
        traceback="Traceback\nValueError: bad",
    )

    def execute(code, stdout, stderr):
        del code
        stdout.write("out")
        stderr.write("trace")
        return SimpleNamespace(ok=False, error=remote_error)

    install_fake_remote(monkeypatch, execute=execute)
    result, payload = invoke_json(["run", "--code", "raise ValueError"])

    assert result.exit_code == ExitCode.REMOTE
    assert payload["stdout"] == "out"
    assert payload["stderr"] == "trace"
    assert payload["error"] == {
        "type": "RemoteExecutionError",
        "phase": "remote_execution",
        "message": "bad",
        "remote_type": "ValueError",
        "traceback": "Traceback\nValueError: bad",
    }


@pytest.mark.parametrize("option", ("--worker", "--background"))
def test_run_worker_json_captures_buffered_output_and_remote_exception(
    monkeypatch, option
):
    remote_error = SimpleNamespace(
        remote_type="ValueError",
        message="bad",
        traceback="Traceback\nValueError: bad",
    )

    def execute_worker(code, stdout, stderr):
        del code
        stdout.write("before\n")
        stderr.write("Traceback\nValueError: bad\n")
        return SimpleNamespace(ok=False, error=remote_error)

    install_fake_remote(monkeypatch, execute_worker=execute_worker)
    result, payload = invoke_json(
        ["run", option, "--code", "print('before'); raise ValueError('bad')"]
    )

    assert result.exit_code == ExitCode.REMOTE
    assert payload["stdout"] == "before\n"
    assert payload["stderr"] == "Traceback\nValueError: bad\n"
    assert payload["error"]["remote_type"] == "ValueError"
    assert payload["error"]["traceback"] == "Traceback\nValueError: bad"


def test_run_text_output_is_not_truncated(monkeypatch):
    def execute(code, stdout, stderr):
        del code
        stdout.write("abcdefghij")
        stderr.write("0123456789")
        return SimpleNamespace(ok=True, error=None)

    install_fake_remote(monkeypatch, execute=execute)
    result = CliRunner().invoke(
        cli,
        ["--max-output", "4", "run", "--code", "pass"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stdout == "abcdefghij"
    assert result.stderr == "0123456789"


@pytest.mark.parametrize("option", ("--worker", "--background"))
def test_run_worker_options_select_worker_client_operation(monkeypatch, option):
    received = {}

    def execute_worker(code, stdout, stderr):
        received["code"] = code
        received["stdout"] = stdout
        received["stderr"] = stderr
        return SimpleNamespace(ok=True, error=None)

    install_fake_remote(monkeypatch, execute_worker=execute_worker)
    result, payload = invoke_json(["run", option, "--code", "print('background')"])

    assert result.exit_code == ExitCode.SUCCESS
    assert received["code"] == "print('background')"
    assert received["stdout"] is not None
    assert received["stderr"] is not None
    assert payload["stdout"] == ""
    assert payload["stderr"] == ""


def test_run_rejects_worker_and_background_together(monkeypatch):
    install_fake_remote(monkeypatch)

    result = CliRunner().invoke(
        cli,
        ["run", "--worker", "--background", "--code", "pass"],
    )

    assert result.exit_code == ExitCode.USAGE
    assert "--worker and --background cannot be used together." in result.output


def test_get_returns_decoded_json_value(monkeypatch):
    def get(expression):
        assert expression == "answer"
        return (41, 42)

    install_fake_remote(monkeypatch, get=get)
    _, payload = invoke_json(["get", "answer"])

    assert payload["value"] == [41, 42]


def test_get_worker_selects_worker_client_operation(monkeypatch):
    received = {}

    def get_worker(expression):
        received["expression"] = expression
        return {"completed": True}

    install_fake_remote(monkeypatch, get_worker=get_worker)
    result, payload = invoke_json(["get", "--worker", "wait_for_work(5)"])

    assert result.exit_code == ExitCode.SUCCESS
    assert received["expression"] == "wait_for_work(5)"
    assert payload["value"] == {"completed": True}


def test_get_rejects_removed_background_option(monkeypatch):
    install_fake_remote(monkeypatch)

    result, payload = invoke_json(["get", "--background", "wait_for_work(5)"])

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["type"] == "UsageError"
    assert "--background" in payload["error"]["message"]


@pytest.mark.parametrize("expression", ["x = 1", "x = 1; x", ""])
def test_get_rejects_non_expressions_without_contacting_remote(monkeypatch, expression):
    def fail(*args, **kwargs):
        del args, kwargs
        pytest.fail("remote operation called")

    monkeypatch.setattr(cli_module, "_call_remote", fail)
    result, payload = invoke_json(["get", expression])

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["type"] == "UsageError"


def test_get_accepts_expression_larger_than_64_kib(monkeypatch):
    expression = "x" * (64 * 1024 + 1)
    received = {}

    def get(remote_expression):
        received["expression"] = remote_expression
        return 42

    install_fake_remote(monkeypatch, get=get)
    result, payload = invoke_json(["get", expression])

    assert result.exit_code == ExitCode.SUCCESS
    assert payload["value"] == 42
    assert received["expression"] == expression


def test_get_text_renders_python_repr(monkeypatch):
    install_fake_remote(monkeypatch, get=lambda expression: {"value"})

    result = CliRunner().invoke(cli, ["get", "value"])

    assert result.output == "{'value'}\n"
    assert result.return_value is None


@pytest.mark.parametrize("value", [{1}, float("nan")])
def test_get_json_rejects_non_json_values(monkeypatch, value):
    install_fake_remote(monkeypatch, get=lambda expression: value)

    result, payload = invoke_json(["get", "value"])

    assert result.exit_code == ExitCode.REMOTE
    assert payload["error"]["type"] == "ValueSerializationError"


def test_protocol_version_error_is_structured(monkeypatch):
    def fail(config, timeout, operation):
        del config, timeout, operation
        raise ProtocolVersionFailure("upgrade")

    monkeypatch.setattr(cli_module, "_call_remote", fail)
    result, payload = invoke_json(["run", "--code", "pass"])

    assert result.exit_code == ExitCode.REMOTE
    assert payload["error"]["type"] == "ProtocolVersionError"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (cli_module.ProtocolVersionError("old"), ProtocolVersionFailure),
        (cli_module.ProtocolError("bad"), RemoteOperationError),
        (
            cli_module.RemoteValueError("NameError", "missing", "traceback"),
            cli_module.RemoteExecutionFailure,
        ),
    ],
)
def test_call_remote_maps_client_protocol_errors(monkeypatch, error, expected):
    def fail(awaitable):
        awaitable.close()
        raise error

    monkeypatch.setattr(cli_module.asyncio, "run", fail)

    with pytest.raises(expected):
        cli_module._call_remote(None, None, None)


def test_remote_operation_total_timeout_phases(monkeypatch):
    class SlowClient:
        async def connect(self, config):
            del config
            await asyncio.sleep(1)

        async def disconnect(self):
            pass

    monkeypatch.setattr(cli_module, "Client", SlowClient)
    monkeypatch.setattr(cli_module, "load_connection_config", lambda config: object())

    with pytest.raises(OperationTimeout) as caught:
        asyncio.run(
            cli_module._run_remote_operation(
                None,
                0.001,
                lambda client: asyncio.sleep(0),
            )
        )
    assert caught.value.phase == "connection"


def test_remote_execution_timeout_phase(monkeypatch):
    class ClientStub:
        async def connect(self, config):
            del config

        async def disconnect(self):
            pass

    monkeypatch.setattr(cli_module, "Client", ClientStub)
    monkeypatch.setattr(cli_module, "load_connection_config", lambda config: object())

    with pytest.raises(OperationTimeout) as caught:
        asyncio.run(
            cli_module._run_remote_operation(
                None,
                0.001,
                lambda client: asyncio.sleep(1),
            )
        )
    assert caught.value.phase == "remote_execution"


def test_remote_operation_with_no_timeout_and_connection_error(monkeypatch):
    class ClientStub:
        fail = False

        async def connect(self, config):
            del config
            if self.fail:
                raise OSError("offline")

        async def disconnect(self):
            pass

    monkeypatch.setattr(cli_module, "Client", ClientStub)
    monkeypatch.setattr(cli_module, "load_connection_config", lambda config: object())

    result = asyncio.run(
        cli_module._run_remote_operation(
            None,
            None,
            lambda client: asyncio.sleep(0, result=42),
        )
    )
    assert result == 42

    ClientStub.fail = True
    with pytest.raises(ConnectionFailure, match="offline"):
        asyncio.run(
            cli_module._run_remote_operation(
                None,
                None,
                lambda client: asyncio.sleep(0),
            )
        )


def test_remote_operation_rejects_expired_budget(monkeypatch):
    class ClientStub:
        async def connect(self, config):
            del config

        async def disconnect(self):
            pass

    monkeypatch.setattr(cli_module, "Client", ClientStub)
    monkeypatch.setattr(cli_module, "load_connection_config", lambda config: object())

    with pytest.raises(OperationTimeout):
        asyncio.run(
            cli_module._run_remote_operation(
                None,
                0,
                lambda client: asyncio.sleep(0),
            )
        )


def test_get_maps_remote_failure(monkeypatch):
    error = cli_module.RemoteError(
        remote_type="NameError",
        message="missing",
        traceback="traceback",
    )

    def fail(config, timeout, operation):
        del config, timeout, operation
        raise cli_module.RemoteExecutionFailure(error)

    monkeypatch.setattr(cli_module, "_call_remote", fail)
    result, payload = invoke_json(["get", "missing"])

    assert result.exit_code == ExitCode.REMOTE
    assert payload["error"]["remote_type"] == "NameError"


@pytest.mark.parametrize(
    ("args", "expected_local_paths", "expected_remote_path"),
    [
        (["tests/hello.txt"], ["tests/hello.txt"], "."),
        (
            ["tests/hello.txt", "tests/hello", "uploads"],
            ["tests/hello.txt", "tests/hello"],
            "uploads",
        ),
    ],
)
def test_upload_calls_client_and_returns_paths(
    monkeypatch,
    args,
    expected_local_paths,
    expected_remote_path,
):
    received = {}

    def upload(local_paths, remote_path):
        received.update(local_paths=local_paths, remote_path=remote_path)

    install_fake_remote(monkeypatch, upload=upload)
    result, payload = invoke_json(["upload", *args])

    assert result.exit_code == ExitCode.SUCCESS
    assert received == {
        "local_paths": expected_local_paths,
        "remote_path": expected_remote_path,
    }
    assert payload["local_paths"] == expected_local_paths
    assert payload["remote_path"] == expected_remote_path


@pytest.mark.parametrize(
    ("args", "expected_remote_paths", "expected_local_path"),
    [
        (["result.csv"], ["result.csv"], "."),
        (
            ["result.csv", "remote_dir", "downloads"],
            ["result.csv", "remote_dir"],
            "downloads",
        ),
    ],
)
def test_download_calls_client_and_returns_paths(
    monkeypatch,
    args,
    expected_remote_paths,
    expected_local_path,
):
    received = {}

    def download(remote_paths, local_path):
        received.update(remote_paths=remote_paths, local_path=local_path)

    install_fake_remote(monkeypatch, download=download)
    result, payload = invoke_json(["download", *args])

    assert result.exit_code == ExitCode.SUCCESS
    assert received == {
        "remote_paths": expected_remote_paths,
        "local_path": expected_local_path,
    }
    assert payload["remote_paths"] == expected_remote_paths
    assert payload["local_path"] == expected_local_path


def test_upload_missing_local_source_is_local_io_error(monkeypatch, tmp_path):
    missing = tmp_path / "missing.txt"

    def fail(*args, **kwargs):
        del args, kwargs
        pytest.fail("remote operation called")

    monkeypatch.setattr(cli_module, "_call_remote", fail)
    result, payload = invoke_json(["upload", str(missing)])

    assert result.exit_code == ExitCode.LOCAL_IO
    assert payload["error"]["type"] == "LocalIOError"
    assert str(missing) in payload["error"]["message"]


@pytest.mark.parametrize(
    ("command", "error", "exit_code", "error_type", "phase"),
    [
        (
            "upload",
            asyncssh.SFTPPermissionDenied("denied"),
            ExitCode.REMOTE,
            "SFTPError",
            "transfer",
        ),
        (
            "download",
            asyncssh.SFTPNoSuchFile("missing"),
            ExitCode.REMOTE,
            "SFTPError",
            "transfer",
        ),
        (
            "download",
            OSError("read-only destination"),
            ExitCode.LOCAL_IO,
            "LocalIOError",
            "local",
        ),
        (
            "download",
            asyncssh.SFTPConnectionLost("lost"),
            ExitCode.CONNECTION,
            "ConnectionError",
            "connection",
        ),
    ],
)
def test_transfer_errors_are_structured(
    monkeypatch,
    command,
    error,
    exit_code,
    error_type,
    phase,
):
    def fail(*args):
        del args
        raise error

    kwargs = {command: fail}
    install_fake_remote(monkeypatch, **kwargs)
    paths = ["tests/hello.txt"] if command == "upload" else ["missing"]
    result, payload = invoke_json([command, *paths])

    assert result.exit_code == exit_code
    assert payload["error"]["type"] == error_type
    assert payload["error"]["phase"] == phase


@pytest.mark.parametrize(
    ("command", "usage"),
    [
        ("upload", "LOCAL_PATH... [REMOTE_PATH]"),
        ("download", "REMOTE_PATH... [LOCAL_PATH]"),
    ],
)
def test_transfer_help(command, usage):
    result = CliRunner().invoke(cli, [command, "--help"])

    assert result.exit_code == 0
    assert usage in result.output
    assert "recursively over SFTP" in result.output


@pytest.mark.asyncio
async def test_console_run_and_get_persist_live_namespace(
    server_instance, connection_config, tmp_path
):
    config = tmp_path / "there.env"
    config.write_text(
        f"THERE_HOST={connection_config.host}\n"
        f"THERE_PORT={connection_config.port}\n"
        f"THERE_USERNAME={connection_config.username}\n"
        f"THERE_PASSWORD={connection_config.password}\n",
        encoding="utf-8",
    )

    run_result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        [
            "--json",
            "--config",
            str(config),
            "run",
            "--code",
            "console_live_value = 41\nprint('ready')",
        ],
    )
    get_result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        [
            "--json",
            "--config",
            str(config),
            "get",
            "console_live_value + 1",
        ],
    )
    background_run_result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        [
            "--json",
            "--config",
            str(config),
            "run",
            "--background",
            "--code",
            "console_background_value = 84\nprint('background ready')",
        ],
    )
    worker_get_result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        [
            "--json",
            "--config",
            str(config),
            "get",
            "--worker",
            "console_background_value // 2",
        ],
    )

    run_payload = json.loads(run_result.output)
    get_payload = json.loads(get_result.output)
    background_run_payload = json.loads(background_run_result.output)
    worker_get_payload = json.loads(worker_get_result.output)
    assert run_result.exit_code == 0
    assert run_payload["stdout"] == "ready\n"
    assert get_result.exit_code == 0
    assert get_payload["value"] == 42
    assert background_run_result.exit_code == 0
    assert background_run_payload["stdout"] == "background ready\n"
    assert background_run_payload["stderr"] == ""
    assert worker_get_result.exit_code == 0
    assert worker_get_payload["value"] == 42


@pytest.mark.asyncio
async def test_console_upload_and_download_files_and_directory(
    server_instance,
    server_config,
    connection_config,
    tmp_path,
):
    del server_instance
    config = tmp_path / "there.env"
    config.write_text(
        f"THERE_HOST={connection_config.host}\n"
        f"THERE_PORT={connection_config.port}\n"
        f"THERE_USERNAME={connection_config.username}\n"
        f"THERE_PASSWORD={connection_config.password}\n",
        encoding="utf-8",
    )
    source_dir = tmp_path / "local-sources"
    source_dir.mkdir()
    local_file = source_dir / "hello.txt"
    local_file.write_text("hello\n", encoding="utf-8")
    local_dir = source_dir / "assets"
    local_dir.mkdir()
    (local_dir / "nested.txt").write_text("nested\n", encoding="utf-8")

    upload_result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        [
            "--json",
            "--config",
            str(config),
            "upload",
            str(local_file),
            str(local_dir),
            ".",
        ],
    )

    assert upload_result.exit_code == 0
    assert (Path(server_config.sftp_root) / "hello.txt").read_text() == "hello\n"
    uploaded_nested = Path(server_config.sftp_root) / "assets/nested.txt"
    assert uploaded_nested.read_text() == "nested\n"

    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    download_result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        [
            "--json",
            "--config",
            str(config),
            "download",
            "hello.txt",
            "assets",
            str(download_dir),
        ],
    )

    assert download_result.exit_code == 0
    assert (download_dir / "hello.txt").read_text() == "hello\n"
    assert (download_dir / "assets/nested.txt").read_text() == "nested\n"


def test_logs_json_returns_structured_snapshot(monkeypatch):
    install_fake_remote(
        monkeypatch,
        logs=lambda max_records: RecentLogsSnapshot(
            text="first\nsecond\n",
            bytes=13,
            records=2,
            truncated=False,
        ),
    )

    result, payload = invoke_json(["logs"])

    assert result.exit_code == ExitCode.SUCCESS
    assert payload["stdout"] == ""
    assert payload["stderr"] == ""
    assert payload["text"] == "first\nsecond\n"
    assert payload["bytes"] == 13
    assert payload["records"] == 2
    assert payload["truncated"] is False
    assert payload["server_truncated"] is False


def test_logs_applies_cli_tail_limit_and_preserves_server_byte_count(monkeypatch):
    install_fake_remote(
        monkeypatch,
        logs=lambda max_records: RecentLogsSnapshot(
            text="1234€",
            bytes=7,
            records=1,
            truncated=False,
        ),
    )

    _, payload = invoke_json(["--max-output", "2", "logs"])

    assert payload["text"] == "��"
    assert payload["bytes"] == 7
    assert payload["truncated"] is True
    assert payload["server_truncated"] is False


def test_logs_reports_server_eviction(monkeypatch):
    install_fake_remote(
        monkeypatch,
        logs=lambda max_records: RecentLogsSnapshot(
            text="latest\n",
            bytes=7,
            records=1,
            truncated=True,
        ),
    )

    _, payload = invoke_json(["logs"])

    assert payload["text"] == "latest\n"
    assert payload["truncated"] is True
    assert payload["server_truncated"] is True


def test_logs_empty_buffer(monkeypatch):
    install_fake_remote(monkeypatch)

    result, payload = invoke_json(["logs"])

    assert result.exit_code == ExitCode.SUCCESS
    assert payload["text"] == ""
    assert payload["bytes"] == 0
    assert payload["truncated"] is False


def test_logs_passes_requested_record_count_to_client(monkeypatch):
    received = {}

    def logs(max_records):
        received["max_records"] = max_records
        return RecentLogsSnapshot(
            text="second\nthird\n",
            bytes=13,
            records=2,
            truncated=False,
        )

    install_fake_remote(monkeypatch, logs=logs)

    result, payload = invoke_json(["logs", "--records", "2"])

    assert result.exit_code == ExitCode.SUCCESS
    assert received["max_records"] == 2
    assert payload["records"] == 2


@pytest.mark.parametrize("records", ["0", "1001", "invalid"])
def test_logs_validates_requested_record_count(records):
    result, payload = invoke_json(["logs", "--records", records])

    assert result.exit_code == ExitCode.USAGE
    assert "--records" in payload["error"]["message"]


def test_logs_text_mode_prints_only_snapshot(monkeypatch):
    install_fake_remote(
        monkeypatch,
        logs=lambda max_records: RecentLogsSnapshot(
            text="warning\n",
            bytes=8,
            records=1,
            truncated=False,
        ),
    )

    result = CliRunner().invoke(cli, ["logs"])

    assert result.exit_code == ExitCode.SUCCESS
    assert result.output == "warning\n"


def test_logs_protocol_error_uses_retrieval_phase(monkeypatch):
    def fail(awaitable):
        awaitable.close()
        raise cli_module.ProtocolError("bad logs")

    monkeypatch.setattr(cli_module.asyncio, "run", fail)

    result, payload = invoke_json(["logs"])

    assert result.exit_code == ExitCode.REMOTE
    assert payload["error"]["phase"] == "log_retrieval"


@pytest.mark.asyncio
@pytest.mark.usefixtures("server_instance")
async def test_console_logs_diagnose_code_before_remote_failure(
    connection_config,
    tmp_path,
):
    config = tmp_path / "there.env"
    config.write_text(
        f"THERE_HOST={connection_config.host}\n"
        f"THERE_PORT={connection_config.port}\n"
        f"THERE_USERNAME={connection_config.username}\n"
        f"THERE_PASSWORD={connection_config.password}\n",
        encoding="utf-8",
    )
    marker = "diagnostic-before-failure"

    run_result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        [
            "--json",
            "--config",
            str(config),
            "run",
            "--code",
            (
                "import logging\n"
                f"logging.warning({marker!r})\n"
                "raise RuntimeError('failed')"
            ),
        ],
    )
    logs_result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        ["--json", "--config", str(config), "logs"],
    )

    assert run_result.exit_code == ExitCode.REMOTE
    assert logs_result.exit_code == ExitCode.SUCCESS
    payload = json.loads(logs_result.output)
    assert marker in payload["text"]
    assert payload["bytes"] == len(payload["text"].encode("utf-8"))


@pytest.mark.parametrize(
    ("args", "input_text", "expected"),
    [
        (["shell", "--command", "printf long"], None, "printf long"),
        (["shell", "-c", "printf short"], None, "printf short"),
        (["shell", "-"], "printf stdin", "printf stdin"),
    ],
)
def test_shell_accepts_inline_command_and_stdin(
    monkeypatch, args, input_text, expected
):
    received = {}

    def execute_shell(command, stdout, stderr):
        received.update(
            command=command,
            direct_stdout=stdout is cli._invocation_stdout,
            direct_stderr=stderr is cli._invocation_stderr,
        )
        stdout.write("out")
        stderr.write("err")
        return ShellResult(returncode=0)

    install_fake_remote(monkeypatch, execute_shell=execute_shell)

    result = CliRunner().invoke(cli, args, input=input_text)

    assert result.exit_code == ExitCode.SUCCESS
    assert received["command"] == expected
    assert received["direct_stdout"]
    assert received["direct_stderr"]
    assert result.stdout == "out"
    assert result.stderr == "err"


def test_shell_reads_positional_file_locally(monkeypatch, tmp_path):
    script = tmp_path / "deploy.sh"
    script.write_text("printf local-script", encoding="utf-8")
    received = {}

    def execute_shell(command, stdout, stderr):
        del stdout, stderr
        received["command"] = command
        return ShellResult(returncode=0)

    install_fake_remote(monkeypatch, execute_shell=execute_shell)

    result = CliRunner().invoke(cli, ["shell", str(script)])

    assert result.exit_code == ExitCode.SUCCESS
    assert received["command"] == "printf local-script"


def test_shell_executes_remote_script_path_through_inline_command(monkeypatch):
    received = {}

    def execute_shell(command, stdout, stderr):
        del stdout, stderr
        received["command"] = command
        return ShellResult(returncode=0)

    install_fake_remote(monkeypatch, execute_shell=execute_shell)

    result = CliRunner().invoke(cli, ["shell", "-c", "./deploy.sh"])

    assert result.exit_code == ExitCode.SUCCESS
    assert received["command"] == "./deploy.sh"


def test_shell_missing_file_is_local_io_with_inline_command_hint():
    result, payload = invoke_json(["shell", "missing-deploy.sh"])

    assert result.exit_code == ExitCode.LOCAL_IO
    assert payload["error"]["type"] == "LocalIOError"
    assert "--command/-c" in payload["error"]["message"]


def test_shell_file_permission_failure_is_local_io(monkeypatch):
    def fail_read_text(self, *, encoding):
        assert encoding == "utf-8"
        raise PermissionError(f"permission denied: {self}")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    result, payload = invoke_json(["shell", "deploy.sh"])

    assert result.exit_code == ExitCode.LOCAL_IO
    assert payload["error"]["type"] == "LocalIOError"
    assert "permission denied" in payload["error"]["message"]


def test_shell_json_captures_raw_streams_and_returncode(monkeypatch):
    def execute_shell(command, stdout, stderr):
        assert command == "command"
        assert stdout is cli._stdout_collector
        assert stderr is cli._stderr_collector
        stdout.write_bytes(b"prefix-\xff-tail")
        stderr.write_bytes(b"error")
        return ShellResult(returncode=0)

    install_fake_remote(monkeypatch, execute_shell=execute_shell)

    result, payload = invoke_json(
        ["--max-output", "5", "shell", "-c", "command"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert payload["returncode"] == 0
    assert payload["stdout"] == "-tail"
    assert payload["stdout_bytes"] == 13
    assert payload["stdout_truncated"] is True
    assert payload["stderr"] == "error"
    assert payload["stderr_bytes"] == 5
    assert payload["stderr_truncated"] is False


@pytest.mark.parametrize("returncode", [1, 127, -15])
def test_shell_nonzero_is_structured_remote_failure(monkeypatch, returncode):
    install_fake_remote(
        monkeypatch,
        execute_shell=lambda command, stdout, stderr: ShellResult(returncode),
    )

    result, payload = invoke_json(["shell", "-c", "exit"])

    assert result.exit_code == ExitCode.REMOTE
    assert payload["ok"] is False
    assert payload["returncode"] == returncode
    assert payload["error"] == {
        "type": "RemoteShellError",
        "phase": "shell_execution",
        "message": f"remote shell exited with status {returncode}",
        "returncode": returncode,
    }


@pytest.mark.parametrize(
    "args",
    [
        ["shell"],
        ["shell", "-c", ""],
        ["shell", ""],
        ["shell", "deploy.sh", "-c", "true"],
        ["shell", "one", "two"],
    ],
)
def test_shell_rejects_missing_empty_and_extra_arguments(args):
    result, payload = invoke_json(args)

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["type"] == "UsageError"


def test_shell_maps_stdin_read_error(monkeypatch):
    class BrokenInput:
        def read(self):
            raise UnicodeError("invalid UTF-8")

    monkeypatch.setattr(cli_module.sys, "stdin", BrokenInput())

    with pytest.raises(LocalIOError, match="invalid UTF-8"):
        cli_module._load_text_source(
            "-",
            None,
            label="Shell input",
            inline_option="--command/-c",
            byte_limit=cli_module.MAX_SHELL_COMMAND_BYTES,
        )


def test_shell_maps_surrogate_escaped_stdin_to_local_io(monkeypatch):
    class SurrogateInput:
        def read(self):
            return "\udcff"

    monkeypatch.setattr(cli_module.sys, "stdin", SurrogateInput())

    with pytest.raises(LocalIOError, match="read input as UTF-8"):
        cli_module._load_text_source(
            "-",
            None,
            label="Shell input",
            inline_option="--command/-c",
            byte_limit=cli_module.MAX_SHELL_COMMAND_BYTES,
        )


def test_text_loader_rejects_non_utf8_inline_text():
    with pytest.raises(click.UsageError, match="valid UTF-8"):
        cli_module._load_text_source(
            None,
            "\udcff",
            label="Shell input",
            inline_option="--command/-c",
            byte_limit=cli_module.MAX_SHELL_COMMAND_BYTES,
        )


def test_invocation_streams_require_active_invocation():
    with pytest.raises(RuntimeError, match="not available"):
        cli.invocation_streams()


def test_shell_protocol_error_uses_shell_phase(monkeypatch):
    def fail(awaitable):
        awaitable.close()
        raise cli_module.ProtocolError("bad shell protocol")

    monkeypatch.setattr(cli_module.asyncio, "run", fail)

    result, payload = invoke_json(["shell", "-c", "command"])

    assert result.exit_code == ExitCode.REMOTE
    assert payload["error"]["phase"] == "shell_execution"


def test_shell_help_describes_sources_and_only_operation_options():
    result = CliRunner().invoke(cli, ["shell", "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "Usage: cli shell [OPTIONS] [FILE]" in result.output
    assert "--command" in result.output
    assert "-c" in result.output
    assert "local script" in result.output
    assert "remote host" in result.output
    assert "--config" not in result.output
    assert "--timeout" not in result.output
    assert "--max-output" not in result.output


def test_run_help_describes_file_inline_and_stdin_sources():
    result = CliRunner().invoke(cli, ["run", "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "Usage: cli run [OPTIONS] [FILE]" in result.output
    assert "--code" in result.output
    assert "-c" in result.output
    assert "local FILE" in result.output
    assert "stdin" in result.output
    assert "--background" in result.output
    assert "--worker" in result.output
    assert "Run like --worker and wait for completion." in result.output


def test_get_help_shows_only_worker_operation_option():
    result = CliRunner().invoke(cli, ["get", "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "--worker" in result.output
    assert "--background" not in result.output
    assert "--config" not in result.output


@pytest.mark.parametrize("command", ["ping", "logs", "shell", "upload", "download"])
def test_unrelated_command_help_omits_background(command):
    result = CliRunner().invoke(cli, [command, "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "--background" not in result.output


@pytest.mark.asyncio
async def test_console_shell_reports_separate_streams_and_failure(
    server_instance,
    connection_config,
    tmp_path,
):
    config = tmp_path / "there.env"
    config.write_text(
        f"THERE_HOST={connection_config.host}\n"
        f"THERE_PORT={connection_config.port}\n"
        f"THERE_USERNAME={connection_config.username}\n"
        f"THERE_PASSWORD={connection_config.password}\n",
        encoding="utf-8",
    )

    result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        [
            "--json",
            "--config",
            str(config),
            "shell",
            "-c",
            "printf out; printf err >&2; exit 9",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == ExitCode.REMOTE
    assert payload["stdout"] == "out"
    assert payload["stderr"] == "err"
    assert payload["returncode"] == 9
    assert server_instance.is_serving()
