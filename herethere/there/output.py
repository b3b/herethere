"""herethere.there.output"""
from collections import deque

from ipywidgets import Output


class LimitedOutput(Output):
    """Widget to capture and display stdout and stderr.
    Output is limited by a `maxlen` number of lines."""

    def __init__(self, maxlen: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._maxlen = maxlen
        self._container = deque(self.outputs, maxlen=self._maxlen)

    def write(self, line: str):
        """Display line."""
        container = self._container
        container.append({"output_type": "stream", "name": "stdout", "text": line})
        self.outputs = list(container)
