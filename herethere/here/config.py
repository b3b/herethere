"""herethere.here.config"""

import logging
from dataclasses import dataclass

from herethere.everywhere import ConnectionConfig
from herethere.everywhere.config import ConnectionConfigError, prefixed_key

logger = logging.getLogger("herethere")


@dataclass(init=False)
class ServerConfig(ConnectionConfig):
    """SSH server configuration."""

    key_path: str = "./ssh_host_key"
    sftp_root: str = "."

    def __init__(
        self,
        username: str,
        password: str,
        *,
        host: str = "127.0.0.1",
        port: int = 8022,
        key_path: str = "./ssh_host_key",
        sftp_root: str = ".",
        chroot: str | None = None,
    ):
        super().__init__(host, port, username, password)
        self.key_path = key_path
        if chroot is not None:
            if sftp_root == ".":
                sftp_root = chroot
                logger.warning(
                    "ServerConfig(chroot=...) is deprecated; use "
                    "ServerConfig(sftp_root=...) instead."
                )
            else:
                logger.warning(
                    "ServerConfig(chroot=...) is deprecated and ignored because "
                    "sftp_root is set."
                )
        self.sftp_root = sftp_root

    @classmethod
    def load_from_dict(cls, *, env: dict[str, str], prefix: str) -> "ServerConfig":
        """Load server config, accepting HERE_CHROOT as a deprecated alias."""
        sftp_root_key = prefixed_key(prefix=prefix, key="sftp_root")
        chroot_key = prefixed_key(prefix=prefix, key="chroot")

        if sftp_root_key in env:
            sftp_root = env[sftp_root_key]
            if chroot_key in env:
                logger.warning(
                    "%s is deprecated and ignored because %s is set.",
                    chroot_key,
                    sftp_root_key,
                )
        elif chroot_key in env:
            sftp_root = env[chroot_key]
            logger.warning(
                "%s is deprecated; use %s instead. This value only controls "
                "the SFTP root for upload/download and is not a process chroot "
                "or sandbox.",
                chroot_key,
                sftp_root_key,
            )
        else:
            sftp_root = "."

        try:
            return cls(
                host=env.get(prefixed_key(prefix=prefix, key="host"), "127.0.0.1"),
                port=env.get(prefixed_key(prefix=prefix, key="port"), 8022),
                username=env[prefixed_key(prefix=prefix, key="username")],
                password=env[prefixed_key(prefix=prefix, key="password")],
                key_path=env.get(
                    prefixed_key(prefix=prefix, key="key_path"), "./ssh_host_key"
                ),
                sftp_root=sftp_root,
            )
        except KeyError as exc:
            raise ConnectionConfigError(
                f"Connection is not configured: {exc} is not set."
            ) from None
