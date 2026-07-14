"""Tests for sandbox.telemetry."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI

import sandbox.telemetry as telemetry
from sandbox.telemetry import flush_telemetry, instrument_app, setup_telemetry


@pytest.fixture
def reset_telemetry_state() -> Iterator[None]:
    """Reset telemetry's process-global setup flags before and after a test."""
    telemetry._setup_attempted = False  # noqa: SLF001
    telemetry._setup_enabled = False  # noqa: SLF001
    yield
    telemetry._setup_attempted = False  # noqa: SLF001
    telemetry._setup_enabled = False  # noqa: SLF001


def test_setup_telemetry_noop_when_disabled(reset_telemetry_state: None) -> None:
    setup_telemetry(
        enabled=False,
        service_name="otari-sandbox-container",
        environment="test",
        otlp_endpoint="http://localhost:4318",
    )

    assert telemetry._setup_enabled is False  # noqa: SLF001

    # Neither helper should raise once setup has (not) run.
    instrument_app(FastAPI())
    flush_telemetry(timeout_ms=200)


def test_instrument_app_and_flush_are_noop_when_never_setup(
    reset_telemetry_state: None,
) -> None:
    instrument_app(FastAPI())
    flush_telemetry(timeout_ms=200)


def test_setup_telemetry_idempotent(reset_telemetry_state: None) -> None:
    setup_telemetry(
        enabled=True,
        service_name="otari-sandbox-container",
        environment="test",
        otlp_endpoint="http://127.0.0.1:4318",
    )
    assert telemetry._setup_enabled is True  # noqa: SLF001
    first_attempted = telemetry._setup_attempted  # noqa: SLF001

    # A second call must be a pure no-op — it must not re-run setup or raise.
    setup_telemetry(
        enabled=True,
        service_name="otari-sandbox-container",
        environment="test",
        otlp_endpoint="http://127.0.0.1:4318",
    )
    assert telemetry._setup_attempted == first_attempted  # noqa: SLF001


def test_setup_telemetry_enabled_smoke(reset_telemetry_state: None) -> None:
    """Setup against an unroutable endpoint must not raise or hang."""
    setup_telemetry(
        enabled=True,
        service_name="otari-sandbox-container",
        environment="test",
        otlp_endpoint="http://127.0.0.1:4318",
    )
    assert telemetry._setup_enabled is True  # noqa: SLF001

    instrument_app(FastAPI())
    # Export against an endpoint with nothing listening should be caught and
    # logged inside flush_telemetry, not propagated — and must not hang.
    flush_telemetry(timeout_ms=200)
