import os

import asyncssh
import pytest


def test_server_is_serving(server_instance):
    assert server_instance.is_serving()
    assert server_instance


@pytest.mark.asyncio
async def test_client_connected(server_instance, connection_config):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None):
        pass


@pytest.mark.asyncio
async def test_pong_returned(server_instance, connection_config):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None) as conn:
        result = await conn.run("ping", check=True)
        assert result.stdout == "pong"


@pytest.mark.asyncio
async def test_line_executed(server_instance, connection_config):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None) as conn:
        result = await conn.run("code", check=True, input="print('hello there')")
        assert result.stdout == "hello there\n"


@pytest.mark.asyncio
async def test_backgroud_line_executed(server_instance, connection_config):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None) as conn:
        result = await conn.run("background", check=True, input="print('hello there')")
        assert result.stdout == "hello there\n"


@pytest.mark.asyncio
async def test_shell_line_executed(server_instance, connection_config):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None) as conn:
        result = await conn.run("shell", check=True, input="echo hello there")
        assert result.stdout == "hello there\n"


@pytest.mark.asyncio
async def test_global_variable_available(server_instance, connection_config):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None) as conn:
        result = await conn.run("code", check=True, input="print(test_variable_in_namespace)")
        assert not result.stderr
        assert result.stdout == 'OK\n'


@pytest.mark.asyncio
async def test_namespace_variable_updated(server_instance, connection_config, tmp_environ):
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None) as conn:
        result = await conn.run(
            "code", check=True, input="print(test_global_variable_updated_var)"
        )
        assert not result.stdout
        assert "NameError:" in result.stderr

        result = await conn.run(
            "code", check=True, input="test_global_variable_updated_var = 1"
        )
        result = await conn.run(
            "code", check=True, input="print(test_global_variable_updated_var)"
        )
        assert result.stdout == "1\n"
        assert result.stderr == ""


@pytest.mark.asyncio
async def test_sftp_file_uploaded(
    server_config, server_instance, client_instance, tmpdir
):
    expected_path = os.path.join(tmpdir, "hello_remote.txt")
    assert not os.path.exists(expected_path)

    async with client_instance as conn:
        async with conn.start_sftp_client() as sftp:
            await sftp.put("tests/hello.txt", "hello_remote.txt")

    assert os.path.exists(expected_path)
