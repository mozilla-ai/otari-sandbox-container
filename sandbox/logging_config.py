"""Structured logging setup for the exec server."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Literal


class JsonLogFormatter(logging.Formatter):
    """Format log records as compact structured JSON."""

    def __init__(self, *, service_name: str, environment: str) -> None:
        super().__init__()
        self._service_name = service_name
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        """Return one serialized JSON log event."""
        created = datetime.fromtimestamp(record.created, tz=UTC)
        timestamp = created.isoformat().replace("+00:00", "Z")
        event: dict[str, object] = {
            "timestamp": timestamp,
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service.name": self._service_name,
            "deployment.environment": self._environment,
        }

        trace_id = getattr(record, "otelTraceID", "")
        span_id = getattr(record, "otelSpanID", "")
        trace_flags = getattr(record, "otelTraceSampled", None)
        if trace_id and trace_id != "0" * 32:
            event["trace_id"] = trace_id
        if span_id and span_id != "0" * 16:
            event["span_id"] = span_id
        if trace_flags is not None and ("trace_id" in event or "span_id" in event):
            event["trace_flags"] = "01" if trace_flags else "00"

        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)

        return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


def configure_logging(
    *,
    log_format: Literal["text", "json"],
    log_level: str,
    service_name: str,
    environment: str,
) -> None:
    """Configure structured JSON logging when requested.

    The requested log level is always applied. Text mode intentionally leaves
    the runtime's existing logging handlers intact.
    """
    level = log_level.upper()
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Uvicorn/FastAPI loggers may have explicit levels, so setting root alone
    # is not enough to make LOG_LEVEL apply consistently in text mode.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logging.getLogger(logger_name).setLevel(level)

    if log_format != "json":
        return

    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter(service_name=service_name, environment=environment))

    root_logger.handlers = [handler]

    for logger_name in ("uvicorn", "uvicorn.error", "fastapi"):
        logger = logging.getLogger(logger_name)
        logger.handlers = []
        logger.propagate = True

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = [handler]
    access_logger.propagate = False
