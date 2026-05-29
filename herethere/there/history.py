"""History of executable %%there Python cells."""

import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RecentThereCell:
    """A recently executed remote Python %%there cell."""

    line: str
    cell: str
    timestamp: float


class RecentThereHistory:
    """Bounded history of recently executed remote Python cells."""

    def __init__(self, maxlen: int = 5) -> None:
        self._cells: deque[RecentThereCell] = deque(maxlen=maxlen)

    def remember(self, line: str, cell: str) -> None:
        """Remember an executable %%there Python cell."""
        self._cells.append(RecentThereCell(line=line, cell=cell, timestamp=time.time()))

    def latest(self) -> RecentThereCell | None:
        """Return the latest remembered cell, if any."""
        if not self._cells:
            return None
        return self._cells[-1]
