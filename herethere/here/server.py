"""herethere.here.server"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import os
import subprocess
import threading
from typing import Any, Callable, Optional, Type

import asyncssh

from herethere.everywhere.code import runcode
from herethere.everywhere.logging import logger
from herethere.here.config import ServerConfig


MAX_COMMAND_LENGTH = 65536  # 65537


async def handle_ping_command(
    process: asyncssh.SSHServerProcess, namespace: dict
):  # pylint: disable=unused-argument
    """Handler for SSH command 'ping'."""
    process.stdout.write("pong")


async def handle_code_command(process: asyncssh.SSHServerProcess, namespace: dict):
    """Handler for SSH command 'code': execute code in the main thread.
    Blocks main thread execution.
    """
    data = await process.stdin.read(MAX_COMMAND_LENGTH)
    runcode(data, stdout=process.stdout, stderr=process.stderr, namespace=namespace)


async def handle_background_code_command(
    process: asyncssh.SSHServerProcess, namespace: dict
):
    """Handler for SSH command 'background': execute code in a separate thread.
    Do not blocks main thread execution.
    """
    server: SSHServerHere = process.channel.get_connection().get_owner()
    data = await process.stdin.read(MAX_COMMAND_LENGTH)
    await server.run_in_executor(
        runcode,
        code=data,
        stdout=process.stdout,
        stderr=process.stderr,
        namespace=namespace,
    )


async def handle_shell_command(
    process: asyncssh.SSHServerProcess, namespace: dict
):  # pylint: disable=unused-argument
    """Handler for SSH command 'shell': execute shell command.
    Do not blocks main thread execution.
    """
    command = await process.stdin.read(MAX_COMMAND_LENGTH)
    proc = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    await process.redirect(stdout=proc.stdout, stderr=proc.stderr)


async def handle_client(process: asyncssh.SSHServerProcess, namespace: dict):
    """SSH requests handler."""

    if namespace is None:
        namespace = {}

    if process.channel._editor:  # pylint: disable=protected-access
        process.channel.set_echo(False)
        process.stdin.channel.set_line_mode(True)

    try:
        processor = {
            "ping": handle_ping_command,
            "code": handle_code_command,
            "background": handle_background_code_command,
            "shell": handle_shell_command,
        }[process.command]
    except KeyError:
        logger.error("Unknown command: %s", process.command[:64])
        process.stderr.write("Unknown command")
        process.exit(0)
        return

    await processor(process, namespace=namespace)
    await process.stdout.drain()
    await process.stderr.drain()
    process.exit(0)


class SFTPServerHere(asyncssh.SFTPServer):
    """SFTP session handler for a given `chroot` directory."""

    def __init__(self, chan: asyncssh.SSHLineEditorChannel, chroot: str):
        os.makedirs(chroot, exist_ok=True)
        super().__init__(chan, chroot=chroot)


class SSHServerHere(asyncssh.SSHServer):
    """SSH server protocol handler with `username` and `password` options."""

    def __init__(self, username: str, password: str, executor: ThreadPoolExecutor):
        self.passwords = {username: password}
        self.executor = executor

    def connection_made(self, conn: asyncssh.SSHServerConnection):
        """Called when a channel is opened successfully."""
        logger.info(
            "SSH connection received from %s.", conn.get_extra_info("peername")[0]
        )

    def connection_lost(self, exc: Optional[Exception]):
        """Called when a channel is closed."""
        if exc:
            logger.info("SSH connection lost: %s.", exc)
        else:
            logger.info("SSH connection closed.")

    def password_auth_supported(self) -> bool:
        """Password authentication is supported."""
        return True

    def begin_auth(self, username: str) -> bool:
        """Allow authentication for the client."""
        return True

    def validate_password(self, username: str, password: str) -> bool:
        """Return whether password is valid for this user."""
        expected = self.passwords.get(username, None)
        return expected and (password == expected)

    async def run_in_executor(self, func: Callable[..., Any], **kwargs: Any):
        """Run func in the thead."""
        await asyncio.get_event_loop().run_in_executor(
            self.executor, partial(func, **kwargs)
        )


class RunningServer:
    """Wrapper for a running SSH server instance."""

    def __init__(
        self, server: asyncio.AbstractServer, namespace, executor: ThreadPoolExecutor
    ):
        self.server = server
        self.executor = executor
        self.namespace = namespace
        self.namespace["ssh_server_closed"] = threading.Event()

    def __getattr__(self, attr):
        return getattr(self.server, attr)

    async def stop(self):
        """Stop SSH server."""
        self.namespace["ssh_server_closed"].set()
        self.server.close()
        self.executor.shutdown(wait=False)
        await self.server.wait_closed()


def generate_private_key(path: str):
    """Generate and save private key to a given location."""
    asyncssh.generate_private_key("ssh-rsa").write_private_key(path)


async def start_server(
    config: ServerConfig,
    namespace: dict = None,
    server_factory: Type[SSHServerHere] = SSHServerHere,
) -> RunningServer:
    """Start SSH server.

    :param config: server configuration options
    :param namespace: dictionary in which Python code commands will be executed
    :param server_factory: optional protocol handler class
    """

    if not issubclass(server_factory, SSHServerHere):
        raise TypeError("server_factory must be a SSHServerHere sublcass.")

    if not os.path.exists(config.key_path):
        logger.info("Generating new private key.")
        generate_private_key(config.key_path)

    if namespace is None:
        namespace = {}

    executor = ThreadPoolExecutor(
        max_workers=64, thread_name_prefix="SSHServerHereThread"
    )

    logger.debug(
        "start_server host=%s port=%s chroot=%s",
        config.host,
        config.port,
        config.chroot,
    )
    server = await asyncssh.create_server(
        host=config.host,
        port=config.port,
        server_host_keys=[config.key_path],
        server_factory=partial(
            server_factory,
            username=config.username,
            password=config.password,
            executor=executor,
        ),
        process_factory=partial(handle_client, namespace=namespace),
        sftp_factory=config.chroot and partial(SFTPServerHere, chroot=config.chroot),
    )
    return RunningServer(server=server, namespace=namespace, executor=executor)
