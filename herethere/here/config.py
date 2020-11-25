"""herethere.here.config"""
from dataclasses import dataclass
from herethere.everywhere import ConnectionConfig


@dataclass
class ServerConfig(ConnectionConfig):
    """Server configuration."""

    key_path: str
    chroot: str
