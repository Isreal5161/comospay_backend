from __future__ import annotations

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any

from app.config.settings import settings


class StructuredFormatter(logging.Formatter):
    """Format log records as structured JSON for production logging."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.__dict__.get("extra", None):
            payload.update(record.__dict__["extra"])
        return json.dumps(payload, default=str)


def _get_log_directory() -> str:
    """Return the configured log directory, creating it if necessary."""
    log_directory = settings.log_directory
    os.makedirs(log_directory, exist_ok=True)
    return log_directory


def get_logger(name: str) -> logging.Logger:
    """Create or return a configured logger instance."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    logger.propagate = False

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(StructuredFormatter())
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        filename=os.path.join(_get_log_directory(), f"{name}.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
    )
    file_handler.setFormatter(StructuredFormatter())
    logger.addHandler(file_handler)

    return logger


def log_api_event(logger: logging.Logger, message: str, **context: Any) -> None:
    """Log API-related events with structured context."""
    logger.info(message, extra={"event_type": "api", **context})


def log_provider_event(logger: logging.Logger, message: str, **context: Any) -> None:
    """Log provider integration events with structured context."""
    logger.info(message, extra={"event_type": "provider", **context})


def log_security_event(logger: logging.Logger, message: str, **context: Any) -> None:
    """Log security-related events with structured context."""
    logger.warning(message, extra={"event_type": "security", **context})


def log_audit_event(logger: logging.Logger, message: str, **context: Any) -> None:
    """Log audit events with structured context."""
    logger.info(message, extra={"event_type": "audit", **context})
