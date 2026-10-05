"""
Unified logger interface for zenstory API.

Provides get_logger() function for all modules to obtain configured loggers.
"""

import logging
import sys
from typing import Any

from utils.request_context import get_log_context

# Frames between the caller of ``log_with_context`` and ``Logger.log``:
# 1 = log_with_context itself, 2 = its caller.
_CALLER_STACKLEVEL = 2


def get_logger(name: str | None = None) -> logging.Logger:
    """
    Get a configured logger instance.

    Args:
        name: Logger name (typically __name__). If None, returns root logger.

    Returns:
        Configured logger instance

    Example:
        >>> from utils.logger import get_logger
        >>> logger = get_logger(__name__)
        >>> logger.info("Application started")
    """
    if name is None:
        return logging.getLogger()
    return logging.getLogger(name)


def log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    exc_info: Any = None,
    **extra_fields: Any,
) -> None:
    """
    Log a message with additional context fields.

    Context fields will be added to the JSON log output. The recorded
    file/line/function point at the caller, not at this helper.

    ``exc_info`` follows ``logging`` semantics. When it is omitted, ERROR and
    above attach the exception currently being handled (if any), so
    ``except`` blocks get a stack trace without repeating ``exc_info=True``.

    Args:
        logger: Logger instance
        level: Log level (logging.INFO, logging.WARNING, etc.)
        message: Log message
        exc_info: Optional exception info, as accepted by ``Logger.log``
        **extra_fields: Additional context fields to include in log

    Example:
        >>> logger = get_logger(__name__)
        >>> log_with_context(
        ...     logger,
        ...     logging.INFO,
        ...     "User logged in",
        ...     user_id="123",
        ...     ip="192.168.1.1"
        ... )
    """
    # Automatically attach request-scoped context for log correlation.
    # Caller-provided fields take precedence.
    merged_fields = {
        **get_log_context(),
        **extra_fields,
    }
    if exc_info is None and level >= logging.ERROR and sys.exc_info()[0] is not None:
        exc_info = True
    extra = {"custom_fields": merged_fields}
    logger.log(
        level,
        message,
        exc_info=exc_info,
        extra=extra,
        stacklevel=_CALLER_STACKLEVEL,
    )
