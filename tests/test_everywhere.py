from io import StringIO
import os
from pathlib import Path

import pytest

from herethere.everywhere import ConnectionConfig, runcode
from herethere.everywhere import config


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
