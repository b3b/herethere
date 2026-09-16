"""Remote value serialization helpers."""

import base64
import json
import keyword
import pickle
import unicodedata
from typing import Any

MAX_VALUE_PAYLOAD_SIZE = 32 * 1024 * 1024


class RemoteValueError(RuntimeError):
    """Raised when remote value computation fails."""

    def __init__(self, error_type: str, message: str, traceback_text: str):
        self.error_type = error_type
        self.remote_message = message
        self.traceback = traceback_text
        super().__init__(f"{error_type}: {message}\n{traceback_text}")


def validate_variable_name(name: str, *, kind: str = "Remote") -> str:
    """Return *name* when it is a valid, non-keyword Python identifier."""
    normalized = unicodedata.normalize("NFKC", name) if isinstance(name, str) else name
    if (
        not isinstance(normalized, str)
        or not normalized.isidentifier()
        or keyword.iskeyword(normalized)
    ):
        raise ValueError(f"{kind} name must be a valid Python variable name: {name!r}")
    return normalized


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


def dumps_put_request(
    name: str,
    value: Any,
    max_payload_size: int = MAX_VALUE_PAYLOAD_SIZE,
) -> str:
    """Serialize a namespace-binding request using the existing value codec."""
    name = validate_variable_name(name)
    value_event = json.loads(dumps_value(value, max_payload_size=max_payload_size))
    return json.dumps({"name": name, "value": value_event})


def loads_put_request(
    message: str,
    max_payload_size: int = MAX_VALUE_PAYLOAD_SIZE,
) -> tuple[str, Any]:
    """Validate and deserialize a namespace-binding request."""
    request = json.loads(message)
    if not isinstance(request, dict) or set(request) != {"name", "value"}:
        raise ValueError("Put request must contain exactly 'name' and 'value'.")

    name = validate_variable_name(request["name"])
    event = request["value"]
    if not isinstance(event, dict) or set(event) != {
        "type",
        "serializer",
        "data",
    }:
        raise ValueError("Put request value must be a complete value event.")
    if event["type"] != "value":
        raise ValueError(f"Unexpected put value event type: {event['type']!r}")
    if event["serializer"] != "pickle":
        raise ValueError(f"Unknown serializer: {event['serializer']!r}")
    if not isinstance(event["data"], str):
        raise TypeError("Put request value data must be a string.")

    try:
        payload = base64.b64decode(event["data"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError("Put request contains invalid base64 value data.") from exc
    if len(payload) > max_payload_size:
        raise ValueError(
            "Remote value pickle payload is too large "
            f"({len(payload)} bytes > {max_payload_size} bytes). "
            "Transfer large data as a file."
        )
    return name, pickle.loads(payload)
