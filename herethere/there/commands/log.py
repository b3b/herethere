"""herethere.there.commands.log"""
# pylint: disable=invalid-name

from herethere.there.commands import there_code_shortcut

LOG_COMMAND_TEMPLATE = r"""
import logging
import sys

rootLogger = logging.getLogger()
handler = logging.StreamHandler(stream=sys.stdout._target_stream)
log_format = '[%(levelname)s] %(asctime)s %(threadName)s %(name)s: %(message)s'
formatter = logging.Formatter(log_format)
handler.setFormatter(formatter)
rootLogger.addHandler(handler)

ssh_server_closed.wait()
rootLogger.removeHandler(handler)
"""


@there_code_shortcut
def log(_) -> str:
    """Listen for log records, send logging output to stdout.
    This command blocks execution thread forever.
    """
    return LOG_COMMAND_TEMPLATE
