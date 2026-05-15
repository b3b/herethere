import asyncio
import asyncssh
from os import environ

import nest_asyncio
import pytest
import pytest_asyncio

from herethere.everywhere import ConnectionConfig
from herethere.here import start_server
from herethere.here.config import ServerConfig
from herethere.there.client import Client
from herethere.there.commands import ContextObject, there_group


@pytest.fixture
def connection_config(monkeypatch, unused_tcp_port):
    config = ConnectionConfig(
        host='localhost',
        port=unused_tcp_port,
        username='here',
        password='there',
    )
    monkeypatch.setenv("HERE_PORT", str(config.port))
    monkeypatch.setenv("THERE_PORT", str(config.port))
    return config


@pytest.fixture
def server_config(tmpdir, connection_config):
    return ServerConfig(
        **connection_config.asdict,
        chroot=tmpdir,
        key_path='tests/key.rsa',
    )


@pytest_asyncio.fixture
async def server_instance(server_config):
    server = await start_server(server_config, namespace={
        'test_variable_in_namespace': 'OK'
    })
    yield server
    await server.stop()


@pytest.fixture
def client_instance(connection_config):
    return asyncssh.connect(**connection_config.asdict, known_hosts=None)


@pytest.fixture
def tmp_environ(mocker):
    mocker.patch.dict("os.environ", {}, clear=True)
    return environ


@pytest_asyncio.fixture
async def there(server_instance, connection_config):
    client = Client()
    await client.connect(connection_config)
    yield client


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


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
