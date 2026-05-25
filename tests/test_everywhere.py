import os
from io import StringIO
from pathlib import Path

import pytest

from herethere.everywhere import ConnectionConfig, config, runcode
from herethere.everywhere.values import (
    RemoteValueError,
    dumps_error,
    dumps_value,
    loads_value,
)

code_with_definition = """
def foo(a, b):
    return a + b

print(foo(1, 2))
"""


@pytest.mark.parametrize(
    "code, expected",
    [
        ('print("1")', "1\n"),
        ('print("1")\nprint("2")', "1\n2\n"),
        (code_with_definition, "3\n"),
    ],
)
def test_runcode_expected_result(code, expected):
    assert runcode(code) == expected


def test_runcode_syntax_error():
    assert "SyntaxError: invalid syntax" in runcode("syntax error here")


@pytest.mark.parametrize(
    "code, expected",
    [
        ('print("1")\nprint("2")', "1\n2\n"),
    ],
)
def test_runcode_expected_io(code, expected):
    stdout = StringIO()
    assert not runcode(code, stdout=stdout)
    assert stdout.getvalue() == expected


def test_runcode_returns_combined_stderr_and_stdout():
    stderr = StringIO()

    assert runcode("print('out')", stderr=stderr) == "\nout\n"


def test_runcode_namespace_used():
    assert "NameError:" in runcode("print(runcode_global_var)")

    namespace = globals()
    global runcode_global_var
    runcode_global_var = 111

    assert "NameError:" in runcode("print(runcode_global_var)")

    assert runcode("print(runcode_global_var)", namespace=namespace) == "111\n"
    assert (
        runcode(
            "runcode_global_var *= 3 ; print(runcode_global_var)", namespace=namespace
        )
        == "333\n"
    )
    assert runcode_global_var == 333


def test_value_serialization_round_trips_nested_values():
    value = {"numbers": [1, 2, 3], "shape": (2, 3), "meta": {"ok": True}}

    assert loads_value(dumps_value(value)) == value


def test_value_serialization_rejects_oversized_payload():
    with pytest.raises(ValueError, match="too large"):
        dumps_value("large", max_payload_size=0)


def test_value_error_deserialization_raises_remote_value_error():
    message = dumps_error(NameError("missing"), "Traceback text")

    with pytest.raises(RemoteValueError, match="NameError: missing"):
        loads_value(message)


def test_value_deserialization_rejects_unknown_serializer():
    message = '{"type": "value", "serializer": "json", "data": ""}'

    with pytest.raises(ValueError, match="Unknown serializer"):
        loads_value(message)


def test_value_deserialization_rejects_unexpected_event_type():
    message = '{"type": "progress"}'

    with pytest.raises(ValueError, match="Unexpected remote value event"):
        loads_value(message)


@pytest.mark.parametrize(
    "path,env,expected",
    (
        (
            "",
            {
                "THERE_HOST": "1",
                "THERE_PORT": "2",
                "THERE_USERNAME": "3",
                "THERE_PASSWORD": "4",
            },
            ConnectionConfig("1", "2", "3", "4"),
        ),
        (
            "tests/connection.env",
            {},
            ConnectionConfig("localhost", "9022", "here", "there"),
        ),
    ),
)
def test_connection_config_loaded(path, env, expected, tmp_environ):
    tmp_environ.update(env)
    assert ConnectionConfig.load(path=path, prefix="there") == expected


def test_connection_config_host_and_port_default_to_localhost():
    config = ConnectionConfig.load_from_dict(
        env={
            "THERE_USERNAME": "here",
            "THERE_PASSWORD": "there",
        },
        prefix="there",
    )

    assert config.host == "127.0.0.1"
    assert config.port == 8022


def test_connection_not_found(tmp_environ):
    with pytest.raises(config.ConnectionConfigError):
        ConnectionConfig.load(path="no-such-config-here", prefix="there")


@pytest.mark.parametrize("prefix", ("", "test"))
def test_connection_config_saved(tmpdir, prefix):
    path = Path(tmpdir) / "test-config-saved.env"
    assert not os.path.exists(path)
    with pytest.raises(config.ConnectionConfigError):
        ConnectionConfig.load(path=path, prefix=prefix)

    ConnectionConfig("localhost", "9022", "here", "there").save(path, prefix=prefix)

    ConnectionConfig.load(path=path, prefix=prefix)


def test_connection_config_save_error(mocker, tmp_path):
    path = tmp_path / "existing.env"
    path.write_text("", encoding="utf-8")
    mocker.patch(
        "herethere.everywhere.config.set_key",
        return_value=(False, None, None),
    )

    with pytest.raises(config.ConnectionConfigError, match="Error while saving"):
        ConnectionConfig("localhost", "9022", "here", "there").save(path)
