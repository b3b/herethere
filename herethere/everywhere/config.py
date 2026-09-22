"""herethere.everywhere.config"""

import os
from dataclasses import MISSING, asdict, dataclass, field, fields
from os import environ
from typing import Any

from dotenv import dotenv_values, find_dotenv, set_key, unset_key


class ConnectionConfigError(Exception):
    """Connection config error."""


def prefixed_key(*, key: str, prefix: str) -> str:
    """Returns key with a prefix and in upper case."""
    if prefix:
        prefix = f"{prefix}_"
    return f"{prefix}{key}".upper()


def load_prefixed_env(*, prefix: str, path: str = None) -> dict[str, str]:
    """Load prefixed dotenv config and overlay process environment variables."""
    if not path:
        path = find_dotenv(f"{prefix}.env", usecwd=True)
    env = dotenv_values(dotenv_path=path)
    env.update(environ)
    return env


@dataclass
class ConnectionConfig:
    """Remote connection configuration."""

    host: str
    port: int
    username: str
    password: str
    host_key_sha256: str | None = field(default=None, kw_only=True)

    def __post_init__(self):
        self.port = int(self.port)
        if self.host_key_sha256 is not None:
            self.host_key_sha256 = self.host_key_sha256.strip() or None

    @property
    def asdict(self) -> dict[str, Any]:
        """Dict represntation of the instance."""
        return asdict(self)

    @property
    def ssh_options(self) -> dict[str, Any]:
        """Connection options accepted directly by AsyncSSH."""
        options = self.asdict
        options.pop("host_key_sha256")
        return options

    @classmethod
    def load(cls, prefix: str = "", path: str = None) -> "ConnectionConfig":
        """Load configuration from the environment, and file with
        configurations variables.
        If `path` is not specified, variables are loaded from a file named {prefix}.env
        in the current directory or any of its parents.

        :param prefix:
            prefix for variables ({PREFIX}_HOST), and a configuration file name
            to search for: {prefix}.env
        :param path: explicit configuration file location
        """
        env = load_prefixed_env(prefix=prefix, path=path)
        return cls.load_from_dict(env=env, prefix=prefix)

    @classmethod
    def load_from_dict(cls, *, env: dict[str, str], prefix: str) -> "ConnectionConfig":
        """Load config from dictionary."""
        values = {}
        for config_field in fields(cls):
            key = prefixed_key(prefix=prefix, key=config_field.name)
            if config_field.name == "host":
                values[config_field.name] = env.get(key, "127.0.0.1")
            elif config_field.name == "port":
                values[config_field.name] = env.get(key, 8022)
            elif key in env:
                values[config_field.name] = env[key]
            elif (
                config_field.default is MISSING
                and config_field.default_factory is MISSING
            ):
                raise ConnectionConfigError(
                    f"Connection is not configured: {key!r} is not set."
                )
        return cls(**values)

    def save(self, path: str, prefix: str = ""):
        """Save config to the given location."""
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8"):
                pass
        saved_keys = dotenv_values(dotenv_path=path)
        for key, value in self.asdict.items():
            env_key = prefixed_key(prefix=prefix, key=key)
            if value is None:
                result = env_key not in saved_keys or unset_key(path, env_key)[0]
            else:
                result = set_key(path, env_key, str(value))[0]
            if not result:
                raise ConnectionConfigError("Error while saving connection config.")
