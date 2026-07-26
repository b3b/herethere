"""Structured remote shell protocol definitions."""

from dataclasses import dataclass

SHELL_PROTOCOL_VERSION = 1
SHELL_STREAM_EVENT = "shell-stream"
SHELL_RESULT_EVENT = "shell-result"
MAX_SHELL_COMMAND_BYTES = 64 * 1024


@dataclass(frozen=True)
class ShellResult:
    """Completion status returned by a structured remote shell command."""

    returncode: int

    @property
    def ok(self) -> bool:
        """Whether the remote shell command exited successfully."""
        return self.returncode == 0


__all__ = (
    "MAX_SHELL_COMMAND_BYTES",
    "SHELL_PROTOCOL_VERSION",
    "SHELL_RESULT_EVENT",
    "SHELL_STREAM_EVENT",
    "ShellResult",
)
