"""herethere.everywhere.config"""
from dataclasses import asdict, dataclass, fields
import os
from os import environ
from typing import Any, Dict

from dotenv import find_dotenv, dotenv_values, set_key


class ConnectionConfigError(Exception):
    """Connection config error."""


def prefixed_key(*, key: str, prefix: str) -> str:
    """Returns key with a prefix and in upper case."""
    if prefix:
        prefix = f"{prefix}_"
    return f"{prefix}{key}".upper()


@dataclass
class ConnectionConfig:
    """Remote connection configuration."""

    host: str
    port: int
    username: str
    password: str

    def __post_init__(self):
        self.port = int(self.port)

    @property
    def asdict(self) -> Dict[str, Any]:
        """Dict represntation of the instance."""
        return asdict(self)

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
        if not path:
            path = find_dotenv(f"{prefix}.env", usecwd=True)
        env = dotenv_values(dotenv_path=path)
        env.update(environ)
        return cls.load_from_dict(env=env, prefix=prefix)

    @classmethod
    def load_from_dict(cls, *, env: Dict[str, str], prefix: str) -> "ConnectionConfig":
        """Load config from dictionary."""
        try:
            return cls(
                *(
                    env[prefixed_key(prefix=prefix, key=field.name)]
                    for field in fields(cls)
                )
            )
        except KeyError as exc:
            raise ConnectionConfigError(
                f"Connection is not configured: {exc} is not set."
            ) from None

    def save(self, path: str, prefix: str = ""):
        """Save config to the given location."""
        if not os.path.exists(path):
            with open(path, "w"):
                pass
        for key, value in self.asdict.items():
            result = set_key(path, prefixed_key(prefix=prefix, key=key), str(value))[0]
            if not result:
                raise ConnectionConfigError("Error while saving connection config.")
