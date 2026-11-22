"""Structured JSON logging configuration.

Provides a single factory function that returns a logger emitting
machine-parseable JSON lines. Centralising this keeps log formatting
consistent across every module (single responsibility).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects.

    Sensitive values must never be passed into log calls; this formatter
    only serialises what it is given and intentionally does not introspect
    arbitrary objects to avoid leaking secrets.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialise a log record to a JSON string.

        Args:
            record: The log record to format.

        Returns:
            A JSON-encoded string representing the log record.
        """
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Merge any structured fields attached via the `extra` argument.
        reserved = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
        reserved.update({"message", "asctime"})
        for key, value in record.__dict__.items():
            if key not in reserved:
                payload[key] = value

        return json.dumps(payload, default=str)


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a logger configured for structured JSON output.

    Args:
        name: The logger name, typically ``__name__``.
        level: The minimum log level to emit.

    Returns:
        A configured :class:`logging.Logger` instance.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(level)
    return logger
