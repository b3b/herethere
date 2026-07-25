"""JSON-lines protocol helpers for structured live-session commands."""

import json
from typing import Any, TextIO


def write_event(writer: TextIO, event: dict[str, Any]) -> None:
    """Write one compact protocol event."""
    writer.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
    writer.write("\n")


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
