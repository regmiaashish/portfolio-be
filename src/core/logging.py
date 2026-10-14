"""Log configuration.

loguru's default handler prints the value of every local variable in a traceback
(`diagnose=True`). That would put visitor data (contact messages, emails, pitch inputs) into
production logs whenever something fails, so tracebacks keep their frames but not the values.
"""

import sys

from loguru import logger


def configure_logging(level: str = "INFO") -> int:
    """Replace loguru's default handler; returns the new handler's id."""
    logger.remove()
    return logger.add(sys.stderr, level=level, backtrace=False, diagnose=False)
