"""herethere.everywhere.redirected_output"""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TextIO


class RedirectedOutputWrapper:
    """Wrapper for I/O stream redirection."""

    def __init__(self, stream: TextIO):
        self._original_stream = stream
        self._redirected_stream = ContextVar(
            f"herethere_redirected_output_{id(self)}",
            default=stream,
        )
        self._redirect_tokens = ContextVar(
            f"herethere_redirected_output_tokens_{id(self)}",
            default=(),
        )

    def __getattr__(self, attr):
        return getattr(self._target_stream, attr)

    def flush(self):
        """Flush the target stream when it supports flushing."""
        if hasattr(self._target_stream, "flush"):
            self._target_stream.flush()

    @property
    def _target_stream(self):
        return self._redirected_stream.get()

    def register(self, stream: TextIO):
        """Start output redirection for the current thread or asyncio task."""
        token = self._redirected_stream.set(stream)
        self._redirect_tokens.set((*self._redirect_tokens.get(), token))

    def unregister(self):
        """Restore the previous output redirection in the current context."""
        tokens = self._redirect_tokens.get()
        if tokens:
            self._redirect_tokens.set(tokens[:-1])
            self._redirected_stream.reset(tokens[-1])


@contextmanager
def redirect_output(stdout: TextIO, stderr: TextIO) -> Iterator[None]:
    """Context manager for temporarily redirecting current context's
    stdout and stderr to other files.
    """
    if not isinstance(sys.stdout, RedirectedOutputWrapper):
        sys.stdout = RedirectedOutputWrapper(sys.stdout)
    if not isinstance(sys.stderr, RedirectedOutputWrapper):
        sys.stderr = RedirectedOutputWrapper(sys.stderr)

    sys.stdout.register(stdout)
    sys.stderr.register(stderr)

    try:
        yield
    finally:
        if isinstance(sys.stdout, RedirectedOutputWrapper):
            sys.stdout.unregister()
        if isinstance(sys.stderr, RedirectedOutputWrapper):
            sys.stderr.unregister()
