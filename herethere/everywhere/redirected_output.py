"""herethere.everywhere.redirected_output"""
from contextlib import contextmanager
import threading
from typing import TextIO, Iterator
import sys


class RedirectedOutputWrapper:
    """Wrapper for I/O stream redirection."""

    def __init__(self, stream: TextIO):
        self._original_stream = stream
        self._redirected_streams = {}

    def __getattr__(self, attr):
        return getattr(self._target_stream, attr)

    @property
    def _target_stream(self):
        return self._redirected_streams.get(
            threading.get_ident(), self._original_stream
        )

    def register(self, stream: TextIO):
        """Start output redirection for current thread."""
        self._redirected_streams[threading.get_ident()] = stream

    def unregister(self):
        """Stop output redirection for current thread."""
        self._redirected_streams.pop(threading.get_ident(), None)


@contextmanager
def redirect_output(stdout: TextIO, stderr: TextIO) -> Iterator[None]:
    """Context manager for temporarily redirecting current thread
    stdout and stderr to another files.
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
