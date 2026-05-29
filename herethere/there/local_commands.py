"""Local-only %%there command registry."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from herethere.there.history import RecentThereHistory


@dataclass(frozen=True)
class LocalThereCommand:
    """Input for a local-only %%there command."""

    line: str
    cell: str
    shell: Any
    history: RecentThereHistory


LocalThereHandler = Callable[[LocalThereCommand], None]

_LOCAL_THERE_HANDLERS: dict[str, LocalThereHandler] = {}


def register_local_there_command(name: str, handler: LocalThereHandler) -> None:
    """Register a local-only %%there subcommand."""
    normalized = name.strip()
    if not normalized:
        raise ValueError("local %%there command name cannot be empty")
    _LOCAL_THERE_HANDLERS[normalized] = handler


def maybe_handle_local_there_command(
    line: str, cell: str | None, shell, history: RecentThereHistory
) -> bool:
    """Return True if a local %%there subcommand handled this cell."""
    parts = line.split()
    if not parts:
        return False

    command = parts[0]
    handler = _LOCAL_THERE_HANDLERS.get(command)
    if handler is None:
        return False

    rest = " ".join(parts[1:])
    handler(
        LocalThereCommand(
            line=rest,
            cell=cell or "",
            shell=shell,
            history=history,
        )
    )
    return True
