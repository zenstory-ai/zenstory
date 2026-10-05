"""
Logging configuration module for zenstory API.

Provides structured JSON logging configuration for Railway deployment.
"""

import json
import logging
import os
import sys
from datetime import UTC, datetime

from utils.request_context import get_log_context
from utils.sanitize import mask_email, redact_log_field

# Attributes every LogRecord carries. Anything else on a record came from
# ``extra=`` and belongs in the JSON output.
_RESERVED_RECORD_ATTRS = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", (), None)).keys()
) | {"message", "asctime", "custom_fields", "request_context", "taskName"}


class RequestContextFilter(logging.Filter):
    """Attach request_id/trace_id/agent_run_id to every record.

    ``log_with_context`` already merges this context, but plain ``logger.*``
    calls (and third-party loggers) would otherwise lose it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_context"):
            record.request_context = get_log_context()
        return True


class JsonFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.

    Formats log records as JSON objects with consistent schema.
    Compatible with Railway's log collection system.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON string."""
        log_obj = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": mask_email(record.getMessage()),
            # Add extra context for better debugging
            "service": os.getenv("APP_NAME", "zenstory API"),
            "environment": os.getenv("ENVIRONMENT", "development"),
            "version": os.getenv("APP_VERSION", "1.0.0"),
        }

        # Add code location info
        log_obj["file"] = f"{record.pathname}"
        log_obj["line"] = record.lineno
        log_obj["function"] = record.funcName

        # Structured fields, lowest precedence first: request context, then
        # plain ``extra=`` kwargs; ``log_with_context`` fields may override.
        fields: dict = dict(getattr(record, "request_context", None) or {})
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS and not key.startswith("_"):
                fields[key] = value
        for key, value in fields.items():
            if key not in log_obj:
                log_obj[key] = redact_log_field(key, value)
        custom_fields = getattr(record, "custom_fields", None)
        if custom_fields:
            for key, value in custom_fields.items():
                log_obj[key] = redact_log_field(key, value)

        # Add exception info if present
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        # Add stack trace if present
        if record.stack_info:
            log_obj["stack"] = self.formatStack(record.stack_info)

        return json.dumps(log_obj, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """
    Configure root logger with JSON formatter.

    Reads LOG_LEVEL from environment variable (default: INFO).
    Configures stream handler to output to stdout.
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Validate log level
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if log_level not in valid_levels:
        log_level = "INFO"

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    root_logger.handlers.clear()

    # Create stream handler (output to stdout for Railway)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(log_level)

    # Set JSON formatter
    formatter = JsonFormatter()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(RequestContextFilter())

    # Add handler to root logger
    root_logger.addHandler(stream_handler)

    # Prevent log propagation from Uvicorn's access logger
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False

    # Route Uvicorn's server/error logs (including "Exception in ASGI
    # application" tracebacks) through the JSON handler instead of plain-text
    # stderr.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # Set log level for common third-party libraries
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# Auto-configure logging on module import
configure_logging()
