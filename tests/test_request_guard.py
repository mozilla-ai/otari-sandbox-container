"""Tests for the DNS-rebinding / cross-origin request guard."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sandbox.request_guard import RequestGuardMiddleware, _hostname_from_host_header


def _guarded_app(**kwargs: object) -> TestClient:
    app = FastAPI()

    @app.api_route("/ping", methods=["GET", "POST"])
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    app.add_middleware(RequestGuardMiddleware, **kwargs)  # type: ignore[arg-type]
    return TestClient(app)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("sandbox:8080", "sandbox"),
        ("localhost", "localhost"),
        ("127.0.0.1:8180", "127.0.0.1"),
        ("[::1]:8080", "::1"),
        ("[::1]", "::1"),
        ("Evil.Example:8180", "evil.example"),
        ("evil.example.", "evil.example"),
        ("sandbox.:8080", "sandbox"),
        ("evil.example:notaport", None),
        ("[::1]junk", None),
        ("", None),
    ],
)
def test_hostname_parsing(header: str, expected: str | None) -> None:
    assert _hostname_from_host_header(header) == expected


@pytest.mark.parametrize(
    "host",
    [
        "sandbox:8080",
        "sandbox",
        "localhost:8180",
        "127.0.0.1:8180",
        "10.0.0.7:8080",
        "[::1]:8180",
        "testserver",
    ],
)
def test_default_allowed_hosts(host: str) -> None:
    client = _guarded_app()
    response = client.get("/ping", headers={"host": host})
    assert response.status_code == 200, host


@pytest.mark.parametrize(
    "host",
    [
        "evil.example:8180",
        "rebind.attacker.test",
        "127.0.0.1.nip.io:8180",
        "localhost.evil.example",
        "sandbox.evil.example:8080",
    ],
)
def test_rebinding_hosts_rejected(host: str) -> None:
    client = _guarded_app()
    response = client.post("/ping", headers={"host": host})
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid host header"}


def test_configured_hosts_allowed() -> None:
    client = _guarded_app(allowed_hosts=["sandbox.ns.svc.cluster.local", "*.internal.example"])
    assert client.get("/ping", headers={"host": "sandbox.ns.svc.cluster.local"}).status_code == 200
    assert client.get("/ping", headers={"host": "a.b.internal.example:80"}).status_code == 200
    # The wildcard must match a subdomain, not a look-alike suffix.
    assert client.get("/ping", headers={"host": "evilinternal.example"}).status_code == 400
    assert client.get("/ping", headers={"host": "internal.example"}).status_code == 400


@pytest.mark.parametrize(
    "origin",
    ["http://evil.example", "http://127.0.0.1:8180", "null"],
)
def test_origin_rejected_by_default(origin: str) -> None:
    client = _guarded_app()
    response = client.post("/ping", headers={"host": "127.0.0.1:8180", "origin": origin})
    assert response.status_code == 403


def test_configured_origin_allowed() -> None:
    client = _guarded_app(allowed_origins=["http://localhost:8180/"])
    ok = client.post("/ping", headers={"host": "localhost:8180", "origin": "http://localhost:8180"})
    assert ok.status_code == 200
    bad = client.post("/ping", headers={"host": "localhost:8180", "origin": "http://evil.example"})
    assert bad.status_code == 403


# ---------------------------------------------------------------------------
# Wired into the real exec server
# ---------------------------------------------------------------------------


@pytest.fixture
def server_client(sessions_root: Path) -> Iterator[TestClient]:
    import sandbox.exec_server as es
    from sandbox.sessions import SessionManager

    es._session_manager = SessionManager(sessions_root=sessions_root)  # noqa: SLF001
    with TestClient(es.app) as c:
        yield c


def test_exec_server_rejects_rebinding_session_create(server_client: TestClient) -> None:
    import sandbox.exec_server as es

    response = server_client.post("/sessions", json={}, headers={"host": "evil.example:8180"})
    assert response.status_code == 400
    assert es._session_manager.count() == 0  # noqa: SLF001


def test_exec_server_rejects_cross_origin_session_create(server_client: TestClient) -> None:
    import sandbox.exec_server as es

    response = server_client.post(
        "/sessions",
        json={},
        headers={"host": "127.0.0.1:8180", "origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert es._session_manager.count() == 0  # noqa: SLF001


def test_exec_server_allows_gateway_host(server_client: TestClient) -> None:
    response = server_client.post("/sessions", json={}, headers={"host": "sandbox:8080"})
    assert response.status_code == 201
