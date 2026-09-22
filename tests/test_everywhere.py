import json
import os
import pickle
from io import StringIO
from pathlib import Path

import pytest

from herethere.everywhere import ConnectionConfig, config, runcode
from herethere.everywhere.values import (
    RemoteValueError,
    dumps_error,
    dumps_put_request,
    dumps_value,
    loads_put_request,
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

    with pytest.raises(RemoteValueError, match="NameError: missing") as caught:
        loads_value(message)
    assert caught.value.error_type == "NameError"
    assert caught.value.remote_message == "missing"
    assert caught.value.traceback == "Traceback text"


def test_value_deserialization_rejects_unknown_serializer():
    message = '{"type": "value", "serializer": "json", "data": ""}'

    with pytest.raises(ValueError, match="Unknown serializer"):
        loads_value(message)


def test_value_deserialization_rejects_unexpected_event_type():
    message = '{"type": "progress"}'

    with pytest.raises(ValueError, match="Unexpected remote value event"):
        loads_value(message)


def test_put_request_round_trips_name_and_value():
    value = {"items": (b"one", None), "enabled": True}

    assert loads_put_request(dumps_put_request("tasks", value)) == ("tasks", value)


@pytest.mark.parametrize("name", ["tasks", "_tasks", "run2", "benchmark_tasks"])
def test_put_request_accepts_valid_variable_names(name):
    assert loads_put_request(dumps_put_request(name, 1)) == (name, 1)


def test_put_request_normalizes_unicode_variable_name():
    request = dumps_put_request("K", 42)

    assert loads_put_request(request) == ("K", 42)


@pytest.mark.parametrize(
    "name",
    [
        "foo.bar",
        "items[0]",
        "1tasks",
        "a-b",
        "class",
        "for",
        "ｃｌａｓｓ",
        "",
        None,
    ],
)
def test_put_request_rejects_invalid_variable_names(name):
    with pytest.raises(ValueError, match="valid Python variable name"):
        dumps_put_request(name, 1)


@pytest.mark.parametrize(
    "put_data, message",
    [
        ([], "exactly 'name' and 'value'"),
        ({"name": "x"}, "exactly 'name' and 'value'"),
        ({"name": "x", "value": {}, "extra": 1}, "exactly 'name' and 'value'"),
        ({"name": "class", "value": {}}, "valid Python variable name"),
        ({"name": "x", "value": []}, "complete value event"),
        (
            {
                "name": "x",
                "value": {"type": "error", "serializer": "pickle", "data": ""},
            },
            "event type",
        ),
        (
            {"name": "x", "value": {"type": "value", "serializer": "json", "data": ""}},
            "Unknown serializer",
        ),
        (
            {
                "name": "x",
                "value": {"type": "value", "serializer": "pickle", "data": 1},
            },
            "must be a string",
        ),
        (
            {
                "name": "x",
                "value": {"type": "value", "serializer": "pickle", "data": "!"},
            },
            "invalid base64",
        ),
    ],
)
def test_put_request_rejects_invalid_envelopes(put_data, message):
    with pytest.raises((TypeError, ValueError), match=message):
        loads_put_request(json.dumps(put_data))


def test_put_request_rejects_oversized_decoded_payload():
    request = dumps_put_request("value", "large")

    with pytest.raises(ValueError, match="too large"):
        loads_put_request(request, max_payload_size=0)


def test_put_request_rejects_invalid_pickle():
    request = {
        "name": "value",
        "value": {"type": "value", "serializer": "pickle", "data": "bm90LXBpY2tsZQ=="},
    }

    with pytest.raises(pickle.UnpicklingError):
        loads_put_request(json.dumps(request))


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("   ", None), (" SHA256:abc ", "SHA256:abc")],
)
def test_connection_config_normalizes_host_key_pin(value, expected):
    config = ConnectionConfig(
        "localhost",
        8022,
        "here",
        "there",
        host_key_sha256=value,
    )

    assert config.host_key_sha256 == expected
    assert "host_key_sha256" not in config.ssh_options


def test_connection_config_loads_optional_host_key_pin():
    config = ConnectionConfig.load_from_dict(
        env={
            "THERE_USERNAME": "here",
            "THERE_PASSWORD": "there",
            "THERE_HOST_KEY_SHA256": " SHA256:abc ",
        },
        prefix="there",
    )

    assert config.host_key_sha256 == "SHA256:abc"


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


def test_connection_config_save_and_remove_host_key_pin(tmp_path, tmp_environ):
    path = tmp_path / "there.env"
    configured = ConnectionConfig(
        "localhost",
        8022,
        "here",
        "there",
        host_key_sha256="SHA256:abc",
    )
    configured.save(path, prefix="there")

    assert ConnectionConfig.load(path=path, prefix="there").host_key_sha256 == (
        "SHA256:abc"
    )

    configured.host_key_sha256 = None
    configured.save(path, prefix="there")

    assert "THERE_HOST_KEY_SHA256" not in path.read_text(encoding="utf-8")
    assert ConnectionConfig.load(path=path, prefix="there").host_key_sha256 is None


def test_connection_config_save_error(mocker, tmp_path):
    path = tmp_path / "existing.env"
    path.write_text("", encoding="utf-8")
    mocker.patch(
        "herethere.everywhere.config.set_key",
        return_value=(False, None, None),
    )

    with pytest.raises(config.ConnectionConfigError, match="Error while saving"):
        ConnectionConfig("localhost", "9022", "here", "there").save(path)
