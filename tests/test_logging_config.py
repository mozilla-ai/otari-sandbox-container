"""Tests for sandbox.logging_config."""

from __future__ import annotations

import json
import logging

from sandbox.logging_config import JsonLogFormatter, configure_logging


def _make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="sandbox.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_basic_fields() -> None:
    formatter = JsonLogFormatter(service_name="otari-sandbox-container", environment="test")
    event = json.loads(formatter.format(_make_record()))

    assert event["severity"] == "INFO"
    assert event["logger"] == "sandbox.test"
    assert event["message"] == "hello world"
    assert event["service.name"] == "otari-sandbox-container"
    assert event["deployment.environment"] == "test"
    assert "trace_id" not in event
    assert "span_id" not in event


def test_json_formatter_includes_trace_context() -> None:
    formatter = JsonLogFormatter(service_name="otari-sandbox-container", environment="test")

    sampled = json.loads(
        formatter.format(
            _make_record(otelTraceID="a" * 32, otelSpanID="b" * 16, otelTraceSampled=True)
        )
    )
    assert sampled["trace_id"] == "a" * 32
    assert sampled["span_id"] == "b" * 16
    assert sampled["trace_flags"] == "01"

    unsampled = json.loads(
        formatter.format(
            _make_record(otelTraceID="a" * 32, otelSpanID="b" * 16, otelTraceSampled=False)
        )
    )
    assert unsampled["trace_flags"] == "00"


def test_json_formatter_omits_null_trace_ids() -> None:
    formatter = JsonLogFormatter(service_name="otari-sandbox-container", environment="test")
    event = json.loads(
        formatter.format(
            _make_record(otelTraceID="0" * 32, otelSpanID="0" * 16, otelTraceSampled=True)
        )
    )

    assert "trace_id" not in event
    assert "span_id" not in event
    assert "trace_flags" not in event


def test_configure_logging_sets_level_regardless_of_format() -> None:
    configure_logging(
        log_format="text",
        log_level="DEBUG",
        service_name="otari-sandbox-container",
        environment="test",
    )

    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
    assert logging.getLogger("uvicorn.access").getEffectiveLevel() == logging.DEBUG


def test_configure_logging_json_mode_wires_formatter() -> None:
    configure_logging(
        log_format="json",
        log_level="INFO",
        service_name="otari-sandbox-container",
        environment="test",
    )

    root_handlers = logging.getLogger().handlers
    assert len(root_handlers) == 1
    assert isinstance(root_handlers[0].formatter, JsonLogFormatter)

    access_logger = logging.getLogger("uvicorn.access")
    assert len(access_logger.handlers) == 1
    assert access_logger.propagate is False
