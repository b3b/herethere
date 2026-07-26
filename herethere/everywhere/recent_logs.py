"""Bounded recent Python logging shared by the server and client."""

import logging
from collections import deque
from dataclasses import dataclass

RECENT_LOGS_COMMAND = "recent-logs"
RECENT_LOGS_RESPONSE_TYPE = "recent-logs"
RECENT_LOGS_PROTOCOL_VERSION = 1
RECENT_LOGS_FORMAT = "[%(levelname)s] %(asctime)s %(threadName)s %(name)s: %(message)s"
DEFAULT_MAX_LOG_RECORDS = 1_000
DEFAULT_MAX_LOG_BYTES = 256 * 1024


@dataclass(frozen=True)
class RecentLogsSnapshot:
    """A finite snapshot of formatted recent log records."""

    text: str
    bytes: int
    records: int
    truncated: bool

    def asdict(self) -> dict[str, object]:
        """Return the wire representation of this snapshot."""
        return {
            "type": RECENT_LOGS_RESPONSE_TYPE,
            "version": RECENT_LOGS_PROTOCOL_VERSION,
            "text": self.text,
            "bytes": self.bytes,
            "records": self.records,
            "truncated": self.truncated,
        }


class RecentLogHandler(logging.Handler):
    """Retain a bounded, thread-safe tail of formatted log records."""

    terminator = "\n"

    def __init__(
        self,
        max_records: int = DEFAULT_MAX_LOG_RECORDS,
        max_bytes: int = DEFAULT_MAX_LOG_BYTES,
    ):
        if max_records < 1:
            raise ValueError("max_records must be positive")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        super().__init__()
        self.max_records = max_records
        self.max_bytes = max_bytes
        self._records: deque[bytes] = deque()
        self._byte_count = 0
        self._truncated = False

    def emit(self, record: logging.LogRecord) -> None:
        """Format and retain one record."""
        try:
            data = (self.format(record) + self.terminator).encode("utf-8")
            if len(data) > self.max_bytes:
                data = (
                    data[-self.max_bytes :]
                    .decode("utf-8", errors="ignore")
                    .encode("utf-8")
                )
                self._truncated = True
            self._records.append(data)
            self._byte_count += len(data)
            while (
                len(self._records) > self.max_records
                or self._byte_count > self.max_bytes
            ):
                self._byte_count -= len(self._records.popleft())
                self._truncated = True
        except Exception:  # pylint: disable=broad-exception-caught
            self.handleError(record)

    def snapshot(self, max_records: int | None = None) -> RecentLogsSnapshot:
        """Return the current records in oldest-to-newest order."""
        if max_records is not None and (
            not isinstance(max_records, int)
            or isinstance(max_records, bool)
            or not 1 <= max_records <= self.max_records
        ):
            raise ValueError(f"max_records must be in the range 1..{self.max_records}")
        self.acquire()
        try:
            records = (
                self._records
                if max_records is None
                else tuple(self._records)[-max_records:]
            )
            data = b"".join(records)
            return RecentLogsSnapshot(
                text=data.decode("utf-8"),
                bytes=len(data),
                records=len(records),
                truncated=self._truncated,
            )
        finally:
            self.release()


def create_recent_log_handler(
    max_records: int = DEFAULT_MAX_LOG_RECORDS,
    max_bytes: int = DEFAULT_MAX_LOG_BYTES,
) -> RecentLogHandler:
    """Create a recent-log handler with the standard streaming log format."""
    handler = RecentLogHandler(max_records=max_records, max_bytes=max_bytes)
    handler.setFormatter(logging.Formatter(RECENT_LOGS_FORMAT))
    return handler


__all__ = (
    "DEFAULT_MAX_LOG_BYTES",
    "DEFAULT_MAX_LOG_RECORDS",
    "RECENT_LOGS_COMMAND",
    "RECENT_LOGS_FORMAT",
    "RECENT_LOGS_PROTOCOL_VERSION",
    "RECENT_LOGS_RESPONSE_TYPE",
    "RecentLogHandler",
    "RecentLogsSnapshot",
    "create_recent_log_handler",
)
