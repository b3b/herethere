"""herethere.here.config"""
from dataclasses import dataclass
from herethere.everywhere import ConnectionConfig


@dataclass
class ServerConfig(ConnectionConfig):
    """SSH server configuration."""

    key_path: str
    chroot: str
