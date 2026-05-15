import asyncio
import shutil
import subprocess
import sys
import threading
from os import environ
from pathlib import Path

import asyncssh
import pytest
import pytest_asyncio

from herethere.everywhere import ConnectionConfig, runcode
from herethere.here import start_server
from herethere.here.config import ServerConfig
from herethere.there.client import Client
from herethere.there.commands import ContextObject, there_group


class CommandClient:
    """Client test double for command-layer tests."""

    def __init__(self, chroot):
        self.chroot = Path(chroot)
        self.namespace = {"ssh_server_closed": threading.Event()}
        self.namespace["ssh_server_closed"].set()

    async def runcode(self, code, stdout=None, stderr=None):
        runcode(
            code,
            stdout=stdout or sys.stdout,
            stderr=stderr or sys.stderr,
            namespace=self.namespace,
        )

    async def shell(self, code, stdout=None, stderr=None):
        result = subprocess.run(
            code,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
        )
        (stdout or sys.stdout).write(result.stdout)
        (stderr or sys.stderr).write(result.stderr)

    async def upload(self, localpaths, remotepath):
        if isinstance(localpaths, str):
            localpaths = [localpaths]

        destination = self.chroot / remotepath
        multiple_sources = len(localpaths) > 1
        if multiple_sources:
            destination.mkdir(parents=True, exist_ok=True)

        for localpath in localpaths:
            source = Path(localpath)
            target = destination / source.name if multiple_sources else destination
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)


@pytest.fixture
def connection_config(monkeypatch, unused_tcp_port):
    config = ConnectionConfig(
        host="localhost",
        port=unused_tcp_port,
        username="here",
        password="there",
    )
    monkeypatch.setenv("HERE_PORT", str(config.port))
    monkeypatch.setenv("THERE_PORT", str(config.port))
    return config


@pytest.fixture
def server_config(tmpdir, connection_config):
    return ServerConfig(
        **connection_config.asdict,
        chroot=tmpdir,
        key_path="tests/key.rsa",
    )


@pytest_asyncio.fixture
async def server_instance(server_config):
    server = await start_server(
        server_config, namespace={"test_variable_in_namespace": "OK"}
    )
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
    await client.disconnect()


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def call_there_group(tmpdir):
    def _callable(args, code):
        there_group(
            args,
            "test",
            standalone_mode=False,
            obj=ContextObject(client=CommandClient(tmpdir), code=code),
        )

    return _callable
