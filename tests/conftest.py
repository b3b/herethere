import asyncssh
from os import environ

import nest_asyncio
import pytest

from herethere.everywhere import ConnectionConfig
from herethere.here import start_server
from herethere.here.config import ServerConfig
from herethere.there.client import Client
from herethere.there.commands import ContextObject, there_group


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


@pytest.fixture
async def there(server_instance, connection_config):
    client = Client()
    await client.connect(connection_config)
    yield client


@pytest.fixture
def nested_event_loop(event_loop):
    nest_asyncio.apply()


@pytest.fixture
def call_there_group(nested_event_loop, there):
    def _callable(args, code):
        there_group(
            args,
            "test",
            standalone_mode=False,
            obj=ContextObject(client=there, code=code),
        )

    return _callable
