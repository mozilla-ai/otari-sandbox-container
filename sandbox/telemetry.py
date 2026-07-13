"""OpenTelemetry setup and app instrumentation helpers.

The setup step configures trace/metric/log providers and library
instrumentors once per process.

The app instrumentation and shutdown flush helpers are no-ops
when telemetry is disabled by configuration.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from fastapi import FastAPI

_logger = logging.getLogger(__name__)

# Bucket boundaries (ms) for histogram instruments. The OTEL default is 16
# fine-grained buckets. These 10 buckets are enough for this service's latency
# distribution while keeping the exported series count smaller.
_HISTOGRAM_BUCKETS_MS: list[float] = [
    5.0,
    10.0,
    25.0,
    50.0,
    100.0,
    250.0,
    500.0,
    1000.0,
    2500.0,
    5000.0,
]

# Unique identifier per process. Generated at module load so every Python
# process gets a distinct value. Used as `service.instance.id` since PID
# alone is unreliable: many container runtimes run this app as PID 1, which
# would otherwise collapse every replica's telemetry into one series.
_INSTANCE_ID = uuid.uuid4().hex[:12]

# Tracks whether telemetry setup has already occurred in this process
# so repeated imports/calls remain idempotent.
_setup_attempted = False
_setup_enabled = False


def _resource_attributes(*, service_name: str, environment: str) -> dict[str, str]:
    """Return the stable application resource identity shared across signals."""
    return {
        "service.name": service_name,
        "service.instance.id": _INSTANCE_ID,
        "deployment.environment": environment,
        "environment": environment,
    }


def setup_telemetry(
    *,
    enabled: bool,
    service_name: str,
    environment: str,
    otlp_endpoint: str,
    otlp_headers: str | None = None,
) -> None:
    """Configure OpenTelemetry providers, exporters, and library instrumentation.

    Idempotent: safe to call multiple times in one process.

    Args:
        enabled: True when telemetry initialization must run.
        service_name: Value for the resource `service.name` attribute.
        environment: Value for the resource `deployment.environment` attribute.
        otlp_endpoint: Base OTLP/HTTP endpoint without signal path suffixes.
        otlp_headers: Optional raw OTLP exporter headers string. Used for
            authenticated OTLP collector endpoints; leave empty when the
            endpoint does not require auth.
    """
    global _setup_attempted, _setup_enabled
    if _setup_attempted:
        return
    _setup_attempted = True

    if not enabled:
        _logger.info("Telemetry disabled by configuration — skipping setup")
        return

    # Imports deferred so a disabled SDK doesn't pay for them, and so a build
    # of this image that hasn't picked up the optional OTel packages yet
    # (see the try/except below) degrades to "telemetry off" instead of
    # failing to import the whole app.
    try:
        from opentelemetry import _logs, metrics, trace
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        _logger.warning(
            "TELEMETRY_ENABLED is set but the OpenTelemetry packages are not "
            "installed in this image — telemetry stays disabled.",
            exc_info=True,
        )
        return

    # Sanity check - sanitize endpoint ending.
    base_endpoint = otlp_endpoint.rstrip("/")
    if otlp_headers:
        # Bridge to the SDK's standard env var so exporters use the
        # OpenTelemetry parser for percent-decoding and edge cases.
        os.environ.setdefault("OTEL_EXPORTER_OTLP_HEADERS", otlp_headers)

    resource = Resource.create(
        _resource_attributes(
            service_name=service_name,
            environment=environment,
        )
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base_endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(tracer_provider)

    histogram_view = View(
        instrument_type=metrics.Histogram,
        aggregation=ExplicitBucketHistogramAggregation(boundaries=_HISTOGRAM_BUCKETS_MS),
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{base_endpoint}/v1/metrics")
            )
        ],
        views=[histogram_view],
    )
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{base_endpoint}/v1/logs"))
    )
    _logs.set_logger_provider(logger_provider)

    otlp_log_handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
    logging.getLogger().addHandler(otlp_log_handler)
    # JSON mode keeps uvicorn.access isolated from the root logger to avoid
    # duplicate console output, so attach the OTLP handler explicitly there.
    logging.getLogger("uvicorn.access").addHandler(otlp_log_handler)

    HTTPXClientInstrumentor().instrument()
    # LoggingInstrumentor injects trace_id/span_id into LogRecord attributes so
    # logs can be correlated to traces. It does NOT format them into the log
    # message — that is a separate formatting concern.
    LoggingInstrumentor().instrument()
    _setup_enabled = True

    _logger.info(
        "OpenTelemetry initialized — service=%s instance=%s environment=%s",
        service_name,
        _INSTANCE_ID,
        environment,
    )


def instrument_app(app: FastAPI) -> None:
    """Attach OpenTelemetry middleware to the FastAPI application instance.

    Args:
        app: FastAPI application instance to instrument.
    """
    if not _setup_enabled:
        return

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


def flush_telemetry(timeout_ms: int = 5000) -> None:
    """Force-flush pending traces, metrics, and logs.

    Args:
        timeout_ms: Maximum flush wait time in milliseconds.
    """
    if not _setup_enabled:
        return

    from opentelemetry import _logs, metrics, trace
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider

    # get_tracer_provider/get_meter_provider are typed to return the API base
    # classes, which don't declare force_flush. setup_telemetry() installs the
    # SDK providers, which do — cast to reflect the real runtime type.
    try:
        cast("TracerProvider", trace.get_tracer_provider()).force_flush(timeout_ms)
    except Exception:  # noqa: BLE001
        _logger.warning("OTEL trace flush failed", exc_info=True)
    try:
        cast("MeterProvider", metrics.get_meter_provider()).force_flush(timeout_ms)
    except Exception:  # noqa: BLE001
        _logger.warning("OTEL metric flush failed", exc_info=True)
    try:
        cast("LoggerProvider", _logs.get_logger_provider()).force_flush(timeout_ms)
    except Exception:  # noqa: BLE001
        _logger.warning("OTEL log flush failed", exc_info=True)
