"""herethere.there.client"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from contextlib import AbstractAsyncContextManager
from typing import TextIO

import asyncssh

from herethere.everywhere.config import ConnectionConfig
from herethere.everywhere.logging import logger


class ConnectionNotConfiguredError(Exception):
    """Connection configuration is missing."""


class PersistentConnection(AbstractAsyncContextManager):
    """SSH connection async context manager with automatic reconnection."""

    def __init__(self):
        self.config: ConnectionConfig | None = None
        self.connection: asyncssh.SSHClientConnection | None = None

    async def __aenter__(self):
        return await self.ensure_connected()

    async def ensure_connected(self):
        """Return an active SSH connection, reconnecting if needed."""
        if await self.check_connection():
            return self.connection
        if self.connection:
            self.close()
        return await self.reconnect()

    async def __aexit__(self, *exc_info):
        pass

    def close(self):
        """Close current connection."""
        if self.connection:
            try:
                self.connection.close()
            except asyncssh.Error:
                pass
        self.connection = None

    async def configure(self, config: ConnectionConfig):
        """Apply new connection config."""
        self.close()
        self.config = config
        return await self.ensure_connected()

    async def check_connection(self) -> bool:
        """Check connection is active."""
        if self.connection:
            try:
                await self.connection.run("ping", check=True)
            except asyncssh.Error:
                logger.debug("SSH connection ping failed.")
            else:
                return True
        return False

    async def reconnect(self):
        """Establish connection."""
        if not self.config:
            raise ConnectionNotConfiguredError("Connection is not configured.")
        self.connection = await asyncssh.connect(**self.config.asdict, known_hosts=None)
        return self.connection


class Client:
    """Client for remote interpreter."""

    def __init__(self):
        self.connection = PersistentConnection()

    async def copy(self) -> Client:
        """Return a copy of the configured connection."""
        client = Client()
        await client.connect(self.connection.config)
        return client

    async def connect(self, config: ConnectionConfig):
        """Connect to remote."""
        await self.connection.configure(config)

    async def disconnect(self):
        """Disconnect from the remote."""
        self.connection.close()

    async def runcode(
        self,
        code: str,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> str:
        """Execute python code on the remote side."""
        await self._execute_code("code", code, stdout, stderr)

    async def runcode_background(
        self,
        code: str,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> str:
        """Execute python code in a separate thread on the remote side."""
        await self._execute_code("background", code, stdout, stderr)

    async def shell(
        self,
        code: str,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> str:
        """Execute shell command on the remote side."""
        await self._execute_code("shell", code, stdout, stderr)

    async def upload(self, localpaths: list[str], remotepath) -> str:
        """Upload files and directories to remote via SFTP."""
        async with self.connection as ssh:
            async with ssh.start_sftp_client() as sftp:
                await sftp.put(
                    localpaths=localpaths,
                    remotepath=remotepath,
                    recurse=True,
                    progress_handler=self.sftp_progress_handler,
                )

    def sftp_progress_handler(self, *args, **kwargs):
        """SFTP uploading progress handler."""
        logger.debug("SFTP progress: %s %s", args, kwargs)

    async def _execute_code(
        self,
        command: str,
        code: str,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ):
        """Execute command with a code on the remote side."""

        if stdout is None:
            stdout = sys.stdout
        if stderr is None:
            stderr = sys.stderr

        async with self.connection as ssh:
            async with ssh.create_process(command) as process:
                process.stdin.write(code)
                # Remote handlers read the submitted code from stdin. Signal
                # end-of-input so they can start or finish execution instead
                # of waiting for more code.
                process.stdin.write_eof()

                async def forward_output(reader, writer):
                    # Stream line-by-line for long-running commands such as
                    # `%there log`. readline() also returns the final partial
                    # line at EOF, so output without a trailing newline is kept.
                    while data := await reader.readline():
                        writer.write(data)
                        if hasattr(writer, "flush"):
                            writer.flush()

                try:
                    await asyncio.gather(
                        forward_output(process.stdout, stdout),
                        forward_output(process.stderr, stderr),
                    )
                    await process.wait()
                except asyncio.CancelledError:
                    process.terminate()
                    # Try to close the remote channel cleanly, but keep
                    # cancellation bounded from the caller's perspective.
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(process.wait(), timeout=1)
                    raise
