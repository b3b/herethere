"""herethere.here.server"""

import asyncio
import os
import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

import asyncssh

from herethere.everywhere.code import runcode
from herethere.everywhere.logging import logger
from herethere.here.config import ServerConfig

MAX_COMMAND_LENGTH = 65536  # 65537
CONNECTION_CLOSE_TIMEOUT = 1.0


async def handle_ping_command(process: asyncssh.SSHServerProcess, namespace: dict):  # pylint: disable=unused-argument
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


async def handle_shell_command(process: asyncssh.SSHServerProcess, namespace: dict):  # pylint: disable=unused-argument
    """Handler for SSH command 'shell': execute shell command.
    Do not blocks main thread execution.
    """
    command = await process.stdin.read(MAX_COMMAND_LENGTH)
    proc = subprocess.Popen(  # pylint: disable=consider-using-with
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

    channel = process.channel
    stdin_channel = process.stdin.channel
    # Configure terminal input only for PTY sessions, where AsyncSSH line
    # editor echo and line-mode controls are meaningful.
    if (
        process.get_terminal_type()
        and hasattr(channel, "set_echo")
        and hasattr(stdin_channel, "set_line_mode")
    ):
        channel.set_echo(False)
        stdin_channel.set_line_mode(True)

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

    def __init__(
        self,
        username: str,
        password: str,
        executor: ThreadPoolExecutor,
        connections: set[asyncssh.SSHServerConnection] | None = None,
    ):
        self.passwords = {username: password}
        self.executor = executor
        self.connections = connections
        self.conn: asyncssh.SSHServerConnection | None = None

    def connection_made(self, conn: asyncssh.SSHServerConnection):
        """Called when a connection is opened successfully."""
        self.conn = conn
        if self.connections is not None:
            self.connections.add(conn)

        peername = conn.get_extra_info("peername")
        peer = peername[0] if peername else "unknown"
        logger.info("SSH connection received from %s.", peer)

    def connection_lost(self, exc: Exception | None):
        """Called when a connection is closed."""
        if self.connections is not None and self.conn is not None:
            self.connections.discard(self.conn)
            self.conn = None
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
        return expected is not None and password == expected

    async def run_in_executor(self, func: Callable[..., Any], **kwargs: Any):
        """Run func in the thead."""
        await asyncio.get_running_loop().run_in_executor(
            self.executor, partial(func, **kwargs)
        )


class RunningServer:
    """Wrapper for a running SSH server instance."""

    def __init__(
        self,
        server: asyncio.AbstractServer,
        namespace,
        executor: ThreadPoolExecutor,
        connections: set[asyncssh.SSHServerConnection],
    ):
        self.server = server
        self.executor = executor
        self.connections = connections
        self.namespace = namespace
        self.namespace["ssh_server_closed"] = threading.Event()

    def __getattr__(self, attr):
        return getattr(self.server, attr)

    @staticmethod
    def _log_wait_closed_results(tasks, action: str):
        for task in tasks:
            try:
                task.result()
            except asyncio.CancelledError:
                logger.debug("SSH connection %s wait was cancelled.", action)
            except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                logger.debug("SSH connection %s finished with error: %r", action, exc)

    async def _wait_for_connection_tasks(self, tasks, action: str, timeout: float):
        if not tasks:
            return set()

        done, pending = await asyncio.wait(
            tasks,
            timeout=timeout,
        )
        self._log_wait_closed_results(done, action)
        return pending

    async def _close_connections(self, timeout: float):
        connections = tuple(self.connections)
        for conn in connections:
            conn.close()

        wait_closed_tasks = [
            asyncio.create_task(
                conn.wait_closed(),
                name="SSH connection wait_closed",
            )
            for conn in connections
        ]
        if wait_closed_tasks:
            pending = await self._wait_for_connection_tasks(
                wait_closed_tasks,
                "close",
                timeout,
            )

            for task, conn in zip(wait_closed_tasks, connections, strict=True):
                if task in pending:
                    logger.debug("SSH connection did not close in time; aborting.")
                    conn.abort()

            if pending:
                still_pending = await self._wait_for_connection_tasks(
                    pending,
                    "abort",
                    timeout,
                )

                for task in still_pending:
                    logger.debug(
                        "SSH connection wait_closed still pending; cancelling task."
                    )
                    task.cancel()

                if still_pending:
                    await asyncio.gather(*still_pending, return_exceptions=True)

    async def stop(self, timeout: float = CONNECTION_CLOSE_TIMEOUT):
        """Stop SSH server."""
        self.namespace["ssh_server_closed"].set()
        self.server.close()
        await self._close_connections(timeout)

        self.executor.shutdown(wait=False)

        try:
            await asyncio.wait_for(
                self.server.wait_closed(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.debug("SSH server wait_closed timed out.")


def generate_private_key(path: str):
    """Generate and save private key to a given location."""
    asyncssh.generate_private_key("ssh-rsa").write_private_key(path)


async def start_server(
    config: ServerConfig,
    namespace: dict = None,
    server_factory: type[SSHServerHere] = SSHServerHere,
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
    connections: set[asyncssh.SSHServerConnection] = set()

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
            connections=connections,
        ),
        process_factory=partial(handle_client, namespace=namespace),
        sftp_factory=config.chroot and partial(SFTPServerHere, chroot=config.chroot),
    )
    return RunningServer(
        server=server,
        namespace=namespace,
        executor=executor,
        connections=connections,
    )
