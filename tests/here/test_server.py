from pathlib import Path
import os

import asyncssh
import pytest

from herethere.here.server import SSHServerHere, start_server


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


@pytest.mark.asyncio
async def test_new_key_generated_if_not_exist(tmpdir, server_config):
    path = Path(tmpdir) / "test_key_do_not_exist.rsa"
    assert not os.path.exists(path)
    server_config.key_path = path

    server_instance = await start_server(server_config)

    try:
        assert os.path.exists(path)
        assert server_instance.is_serving()
    finally:
        await server_instance.stop()


class CustomSSHServerHere(SSHServerHere):

    test_events = []

    def connection_made(self, *args, **kwargs):
        CustomSSHServerHere.test_events.append('connection_made')


@pytest.mark.asyncio
async def test_custom_server_class_used(server_config, connection_config):
    assert not CustomSSHServerHere.test_events

    await start_server(server_config, server_factory=CustomSSHServerHere)
    async with asyncssh.connect(**connection_config.asdict, known_hosts=None):
        pass

    assert CustomSSHServerHere.test_events == ['connection_made']


@pytest.mark.asyncio
async def test_custom_server_class_bad_type(server_config):
    with pytest.raises(TypeError):
        await start_server(server_config, server_factory=object)
