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
from herethere.there import cli as cli_module
from herethere.there.cli import (
    MAX_MAX_OUTPUT,
    BoundedTextCollector,
    ConnectionFailure,
    ExitCode,
    LocalIOError,
    OperationTimeout,
    PluginError,
    ProtocolVersionFailure,
    RemoteOperationError,
    cli,
    load_connection_config,
    remote_options,
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
    execute=None,
    get=None,
    upload=None,
    download=None,
):
    class FakeClient:
        async def execute(self, code, stdout=None, stderr=None):
            if execute is not None:
                return execute(code, stdout, stderr)
            return SimpleNamespace(ok=True, error=None)

        async def get(self, expression):
            if get is not None:
                return get(expression)
            return 42

        async def upload(self, local_paths, remote_path):
            if upload is not None:
                return upload(local_paths, remote_path)
            return None

        async def download(self, remote_paths, local_path):
            if download is not None:
                return download(remote_paths, local_path)
            return None

    def call(config, timeout, operation, **kwargs):
        del config, timeout
        assert kwargs.get("operation_phase", "remote_execution") in {
            "remote_execution",
            "transfer",
        }
        return asyncio.run(operation(FakeClient()))

    monkeypatch.setattr(cli_module, "_call_remote", call)


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
    assert "--format [text|json]" in result.output
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
    @remote_options
    def remote(config, timeout, max_output):
        del config, timeout, max_output

    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("remote", remote),)
    )
    result, payload = invoke_json(["remote", "--max-output", value])
    assert result.exit_code == ExitCode.USAGE
    assert "--max-output" in payload["error"]["message"]


def test_max_output_equals_form_bounds_captured_plugin_output(monkeypatch):
    @click.command()
    @remote_options
    def noisy(config, timeout, max_output):
        del config, timeout, max_output
        click.echo("abcdefghij", nl=False)

    monkeypatch.setattr(
        cli_module, "_entry_points", lambda: (EntryPointStub("noisy", noisy),)
    )
    result, payload = invoke_json(["noisy", "--max-output=4"])

    assert result.exit_code == 0
    assert payload["stdout"] == "ghij"
    assert payload["stdout_bytes"] == 10
    assert payload["stdout_truncated"] is True


def test_format_equals_form_is_parsed_by_click():
    result = CliRunner().invoke(cli, ["--format=json", "missing"])
    assert result.exit_code == ExitCode.USAGE
    assert json.loads(result.output)["error"]["type"] == "UsageError"


def test_remote_options_parse_consistently():
    received = {}

    @click.command()
    @remote_options
    def command(config, timeout, max_output):
        received.update(
            config=config,
            timeout=timeout,
            max_output=max_output,
        )

    result = CliRunner().invoke(
        command,
        ["--config", "custom.env", "--timeout", "1.5", "--max-output", "12"],
    )

    assert result.exit_code == 0
    assert received == {
        "config": Path("custom.env"),
        "timeout": 1.5,
        "max_output": 12,
    }


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
        (["run", "-"], "print(2)", "print(2)"),
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


def test_run_maps_local_file_error():
    result, payload = invoke_json(["run", "missing-app.py"])

    assert result.exit_code == ExitCode.LOCAL_IO
    assert payload["error"]["type"] == "LocalIOError"


def test_run_maps_stdin_read_error(monkeypatch):
    class BrokenInput:
        def read(self):
            raise OSError("broken input")

    monkeypatch.setattr(cli_module.sys, "stdin", BrokenInput())

    with pytest.raises(LocalIOError, match="broken input"):
        cli_module._read_run_source("-", None)


def test_run_rejects_oversized_input():
    result, payload = invoke_json(["run", "--code", "x" * (65536 + 1)])

    assert result.exit_code == ExitCode.USAGE
    assert "too large" in payload["error"]["message"]


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


def test_get_returns_decoded_json_value(monkeypatch):
    def get(expression):
        assert expression == "answer"
        return (41, 42)

    install_fake_remote(monkeypatch, get=get)
    _, payload = invoke_json(["get", "answer"])

    assert payload["value"] == [41, 42]


@pytest.mark.parametrize("expression", ["x = 1", "x = 1; x", ""])
def test_get_rejects_non_expressions_without_contacting_remote(monkeypatch, expression):
    def fail(*args, **kwargs):
        del args, kwargs
        pytest.fail("remote operation called")

    monkeypatch.setattr(cli_module, "_call_remote", fail)
    result, payload = invoke_json(["get", expression])

    assert result.exit_code == ExitCode.USAGE
    assert payload["error"]["type"] == "UsageError"


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
            "run",
            "--config",
            str(config),
            "--code",
            "console_live_value = 41\nprint('ready')",
        ],
    )
    get_result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        [
            "--json",
            "get",
            "--config",
            str(config),
            "console_live_value + 1",
        ],
    )

    run_payload = json.loads(run_result.output)
    get_payload = json.loads(get_result.output)
    assert run_result.exit_code == 0
    assert run_payload["stdout"] == "ready\n"
    assert get_result.exit_code == 0
    assert get_payload["value"] == 42


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
            "upload",
            "--config",
            str(config),
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
            "download",
            "--config",
            str(config),
            "hello.txt",
            "assets",
            str(download_dir),
        ],
    )

    assert download_result.exit_code == 0
    assert (download_dir / "hello.txt").read_text() == "hello\n"
    assert (download_dir / "assets/nested.txt").read_text() == "nested\n"
