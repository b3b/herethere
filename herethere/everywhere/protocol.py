"""JSON-lines protocol helpers for structured live-session commands."""

import json
from dataclasses import dataclass
from typing import Any, Literal, TextIO

MAX_WORKER_OUTPUT_BYTES = 1024 * 1024
MAX_WORKER_OUTPUT_EVENTS = 16_384
WORKER_OUTPUT_TRUNCATION_MARKER = "[herethere: worker output truncated]\n"
StreamName = Literal["stdout", "stderr"]


@dataclass(frozen=True)
class CapturedStreamEvent:
    """One ordered stdout or stderr write captured during worker execution."""

    stream: StreamName
    data: str

    def asdict(self) -> dict[str, str]:
        """Return the structured stream-event representation."""
        return {"type": "stream", "stream": self.stream, "data": self.data}


class _CapturedStream:
    """Text writer which records writes in a shared ordered collector."""

    def __init__(self, collector: "OrderedBoundedOutputCollector", stream: StreamName):
        self.collector = collector
        self.stream = stream

    @property
    def encoding(self) -> str:
        """Report the encoding used for output-bound accounting."""
        return "utf-8"

    def writable(self) -> bool:
        """Return whether this stream accepts text writes."""
        return True

    def write(self, data: str) -> int:
        """Capture one text fragment and report it as consumed."""
        return self.collector.write(self.stream, data)

    def flush(self) -> None:
        """Provide the standard text-writer flush interface."""


class OrderedBoundedOutputCollector:
    """Capture bounded stdout/stderr payload while preserving write order."""

    def __init__(
        self,
        byte_limit: int = MAX_WORKER_OUTPUT_BYTES,
        event_limit: int = MAX_WORKER_OUTPUT_EVENTS,
    ):
        if byte_limit < 1:
            raise ValueError("byte_limit must be positive")
        if event_limit < 1:
            raise ValueError("event_limit must be positive")
        self.byte_limit = byte_limit
        self.event_limit = event_limit
        self._events: list[tuple[StreamName, bytearray]] = []
        self.payload_bytes = 0
        self.truncated = False
        self.stdout = _CapturedStream(self, "stdout")
        self.stderr = _CapturedStream(self, "stderr")

    @property
    def events(self) -> list[CapturedStreamEvent]:
        """Return immutable views of the retained ordered stream events."""
        return [
            CapturedStreamEvent(stream, bytes(data).decode("utf-8"))
            for stream, data in self._events
        ]

    def write(self, stream: StreamName, data: str) -> int:
        """Capture as much of one write as permitted by both limits."""
        if not isinstance(data, str):
            raise TypeError("write() argument must be str")
        consumed = len(data)
        if not data or self.truncated:
            return consumed

        encoded = data.encode("utf-8")
        remaining = self.byte_limit - self.payload_bytes
        crosses_byte_limit = len(encoded) > remaining
        retained = (
            self._utf8_prefix(encoded, remaining) if crosses_byte_limit else encoded
        )

        can_coalesce = bool(self._events and self._events[-1][0] == stream)
        crosses_event_limit = bool(
            retained and not can_coalesce and len(self._events) >= self.event_limit
        )
        if not crosses_event_limit and retained:
            self._append(stream, retained)

        if crosses_byte_limit or crosses_event_limit:
            self._truncate()
        return consumed

    @staticmethod
    def _utf8_prefix(data: bytes, limit: int) -> bytes:
        """Return the largest valid UTF-8 prefix fitting within ``limit`` bytes."""
        return data[:limit].decode("utf-8", errors="ignore").encode("utf-8")

    def _append(self, stream: StreamName, data: bytes) -> None:
        """Append or coalesce one retained user-output fragment."""
        self.payload_bytes += len(data)
        if self._events and self._events[-1][0] == stream:
            self._events[-1][1].extend(data)
            return
        self._events.append((stream, bytearray(data)))

    def _truncate(self) -> None:
        """Stop capture and append the one fixed stderr truncation marker."""
        self.truncated = True
        self._events.append(
            ("stderr", bytearray(WORKER_OUTPUT_TRUNCATION_MARKER.encode("utf-8")))
        )


def write_event(writer: TextIO, event: dict[str, Any]) -> None:
    """Write one compact protocol event."""
    writer.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
    writer.write("\n")


def decode_request_object(request_text: str) -> dict[str, Any]:
    """Decode one JSON request object with a stable validation error."""
    try:
        request = json.loads(request_text)
    except json.JSONDecodeError as exc:
        raise ValueError("request must be valid JSON") from exc
    if not isinstance(request, dict):
        raise TypeError("request must be a JSON object")
    return request


class EventStream:
    """Text writer which emits user output as JSON-lines stream events."""

    def __init__(self, writer: TextIO, stream: str):
        self.writer = writer
        self.stream = stream

    def write(self, data: str) -> int:
        """Write one output chunk as a stream event."""
        if data:
            write_event(
                self.writer,
                {"type": "stream", "stream": self.stream, "data": data},
            )
        return len(data)

    def flush(self) -> None:
        """Flush the protocol transport when supported."""
        if hasattr(self.writer, "flush"):
            self.writer.flush()
