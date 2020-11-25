import asyncssh
from os import environ

import pytest

from herethere.everywhere import ConnectionConfig
from herethere.here import start_server
from herethere.here.config import ServerConfig


@pytest.fixture
def connection_config():
    return ConnectionConfig(
        host='localhost',
        port=9022,
        username='here',
        password='there',
    )


@pytest.fixture
def server_config(tmpdir, connection_config):
    return ServerConfig(
        **connection_config.asdict,
        chroot=tmpdir,
        key_path='tests/key.rsa',
    )


@pytest.fixture
async def server_instance(server_config):
    server = await start_server(server_config, namespace={
        'test_variable_in_namespace': 'OK'
    })
    yield server
    server.close()
    await server.wait_closed()


@pytest.fixture
def client_instance(connection_config):
    return asyncssh.connect(**connection_config.asdict, known_hosts=None)


@pytest.fixture
def tmp_environ(mocker):
    mocker.patch.dict("os.environ", {}, clear=True)
    return environ
