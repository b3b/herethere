"""herethere.there.commands.log"""
# pylint: disable=invalid-name

from herethere.there.commands import there_code_shortcut

LOG_COMMAND_TEMPLATE = r"""
import errno
import logging
import sys
import threading

stop_log_listener = threading.Event()
CLOSED_STREAM_ERRNOS = {
    errno.EPIPE,
    errno.ECONNRESET,
    errno.ECONNABORTED,
    errno.ENOTCONN,
}


def is_closed_stream_error(error):
    # The SSH channel can be closed while this root logger handler is still
    # installed. Treat only connection-style write failures as a signal to
    # detach, and let unrelated logging errors use normal logging reporting.
    return isinstance(
        error,
        (BrokenPipeError, ConnectionResetError, ConnectionAbortedError),
    ) or (
        isinstance(error, OSError)
        and error.errno in CLOSED_STREAM_ERRNOS
    )


class SSHLogHandler(logging.StreamHandler):
    def handleError(self, record):
        error = sys.exc_info()[1]
        if is_closed_stream_error(error):
            stop_log_listener.set()
            return
        super().handleError(record)


rootLogger = logging.getLogger()
handler = SSHLogHandler(stream=sys.stdout._target_stream)
log_format = '[%(levelname)s] %(asctime)s %(threadName)s %(name)s: %(message)s'
formatter = logging.Formatter(log_format)
handler.setFormatter(formatter)

try:
    rootLogger.addHandler(handler)
    # Wake on a broken client stream or on whole-server shutdown. threading.Event
    # cannot wait on two events directly, so use a short blocking wait.
    while not ssh_server_closed.is_set() and not stop_log_listener.is_set():
        stop_log_listener.wait(0.2)
finally:
    rootLogger.removeHandler(handler)
    handler.close()
"""


@there_code_shortcut
def log(_) -> str:
    """Listen for log records, send logging output to stdout.
    This command blocks the execution thread until stopped.
    """
    return LOG_COMMAND_TEMPLATE
