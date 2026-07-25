"""Remote value serialization helpers."""

import base64
import json
import pickle
from typing import Any

MAX_VALUE_PAYLOAD_SIZE = 32 * 1024 * 1024


class RemoteValueError(RuntimeError):
    """Raised when remote value computation fails."""

    def __init__(self, error_type: str, message: str, traceback_text: str):
        self.error_type = error_type
        self.remote_message = message
        self.traceback = traceback_text
        super().__init__(f"{error_type}: {message}\n{traceback_text}")


def dumps_value(value: Any, max_payload_size: int = MAX_VALUE_PAYLOAD_SIZE) -> str:
    """Serialize a remote value event as JSON."""
    payload = pickle.dumps(value)
    if len(payload) > max_payload_size:
        raise ValueError(
            "Remote value pickle payload is too large "
            f"({len(payload)} bytes > {max_payload_size} bytes). "
            "Return a smaller summary or transfer large data as a file."
        )
    return json.dumps(
        {
            "type": "value",
            "serializer": "pickle",
            "data": base64.b64encode(payload).decode("ascii"),
        }
    )


def dumps_error(exc: BaseException, traceback_text: str) -> str:
    """Serialize a remote exception event as JSON."""
    return json.dumps(
        {
            "type": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback_text,
        }
    )


def loads_value(message: str) -> Any:
    """Deserialize a remote value event or raise its remote error."""
    event = json.loads(message)
    event_type = event.get("type")

    if event_type == "value":
        serializer = event.get("serializer")
        if serializer != "pickle":
            raise ValueError(f"Unknown serializer: {serializer!r}")

        payload = base64.b64decode(event["data"].encode("ascii"))
        return pickle.loads(payload)

    if event_type == "error":
        raise RemoteValueError(
            event.get("error_type", "Exception"),
            event.get("message", ""),
            event.get("traceback", ""),
        )

    raise ValueError(f"Unexpected remote value event: {event!r}")
