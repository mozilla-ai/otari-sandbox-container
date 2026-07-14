"""Pytest configuration shared across all sandbox-image tests."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

_OBSERVED_LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "fastapi")


@pytest.fixture(autouse=True)
def _restore_logging_state() -> Iterator[None]:
    """Snapshot/restore root + uvicorn*/fastapi logger config around each test.

    ``sandbox.exec_server`` calls ``configure_logging()`` once at module
    import time, which mutates global ``logging`` state (handlers, levels).
    Without this, that state — and whatever a given test does to it — leaks
    into every other test in the suite regardless of import order.
    """
    loggers = [logging.getLogger(name) for name in _OBSERVED_LOGGERS]
    snapshot = [(lg, lg.level, list(lg.handlers), lg.propagate) for lg in loggers]
    yield
    for lg, level, handlers, propagate in snapshot:
        lg.setLevel(level)
        lg.handlers = handlers
        lg.propagate = propagate


@pytest.fixture
def sessions_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect SessionManager's per-session storage into a tmp dir.

    Tests that build a real :class:`SessionManager` (rather than passing
    individual workspace paths to lower-level helpers) request this
    fixture so the manager doesn't try to create directories under
    ``/var/sandbox`` on the developer's host.
    """
    root = tmp_path / "sandbox-sessions"
    root.mkdir()
    monkeypatch.setenv("SANDBOX_SESSIONS_ROOT", str(root))
    return root
