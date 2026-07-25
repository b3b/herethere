"""Structured live-session execution helpers shared by the SSH server."""

import traceback
from dataclasses import dataclass
from typing import Any, TextIO

from herethere.everywhere.redirected_output import redirect_output

PROTOCOL_EXECUTE_COMMAND = "execute-v1"
MAX_TRACEBACK_BYTES = 64 * 1024


@dataclass(frozen=True)
class LiveError:
    """Structured exception information produced in the live interpreter."""

    remote_type: str
    message: str
    traceback: str

    def asdict(self) -> dict[str, str]:
        """Return the protocol representation."""
        return {
            "remote_type": self.remote_type,
            "message": self.message,
            "traceback": self.traceback,
        }


def bounded_utf8_tail(text: str, limit: int) -> str:
    """Return at most ``limit`` trailing UTF-8 bytes as valid text."""
    return text.encode("utf-8")[-limit:].decode("utf-8", errors="ignore")


def execute_live(
    code: str,
    namespace: dict[str, Any],
    stdout: TextIO,
    stderr: TextIO,
) -> LiveError | None:
    """Execute code in a namespace and report exceptions without hiding them."""
    try:
        compiled = compile(code, "<string>", "exec")
        with redirect_output(stdout=stdout, stderr=stderr):
            exec(compiled, namespace)  # pylint: disable=exec-used
    except Exception as exc:  # pylint: disable=broad-exception-caught
        exception_traceback = exc.__traceback__.tb_next
        traceback_text = "".join(
            traceback.format_exception(type(exc), exc, exception_traceback)
        )
        traceback_text = bounded_utf8_tail(traceback_text, MAX_TRACEBACK_BYTES)
        stderr.write(traceback_text)
        return LiveError(
            remote_type=type(exc).__name__,
            message=str(exc),
            traceback=traceback_text,
        )
    return None
