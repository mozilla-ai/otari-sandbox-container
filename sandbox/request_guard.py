"""Reject requests that can only have come from a browser attack.

The API is unauthenticated and assumes it is reachable only by a trusted
caller (the gateway) over a private network or a loopback port mapping.
That assumption breaks for a developer whose browser can reach the port: a
web page can use DNS rebinding to point its own hostname at ``127.0.0.1``
and then talk to the sandbox same-origin, i.e. create sessions, run code,
and read files.

This middleware closes that hole with two checks:

* **Host allowlist.** A rebinding page always sends its own hostname in
  ``Host`` (e.g. ``evil.example:8180``). We accept only hosts that an
  attacker cannot get a browser to send: IP literals, ``localhost``,
  single-label names such as a Docker / Kubernetes service name
  (``sandbox``), plus anything listed in ``SANDBOX_ALLOWED_HOSTS``.
* **Origin rejection.** Server-to-server clients never send ``Origin``;
  browsers send it on every cross-origin request and every non-GET
  request. Any ``Origin`` not listed in ``SANDBOX_ALLOWED_ORIGINS`` is
  refused, which also blocks plain cross-site requests (CSRF) aimed at
  ``http://127.0.0.1:<port>``.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
from collections.abc import Iterable

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

ALLOWED_HOSTS_ENV = "SANDBOX_ALLOWED_HOSTS"
ALLOWED_ORIGINS_ENV = "SANDBOX_ALLOWED_ORIGINS"


def _split_env(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def allowed_hosts_from_env() -> list[str]:
    return _split_env(os.environ.get(ALLOWED_HOSTS_ENV))


def allowed_origins_from_env() -> list[str]:
    return _split_env(os.environ.get(ALLOWED_ORIGINS_ENV))


def _hostname_from_host_header(host: str) -> str | None:
    """Strip the port (and IPv6 brackets) from a ``Host`` header value."""
    host = host.strip().lower()
    if not host:
        return None
    if host.startswith("["):
        end = host.find("]")
        if end == -1:
            return None
        rest = host[end + 1 :]
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return None
        return host[1:end]
    name, sep, port = host.rpartition(":")
    if sep:
        if not port.isdigit():
            return None
        host = name
    # A fully qualified name may carry a trailing root dot.
    return host.removesuffix(".") or None


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


class RequestGuardMiddleware:
    """Pure ASGI middleware enforcing the Host allowlist and Origin rejection."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: Iterable[str] = (),
        allowed_origins: Iterable[str] = (),
    ) -> None:
        self.app = app
        self._exact_hosts: set[str] = set()
        self._suffix_hosts: list[str] = []
        for entry in allowed_hosts:
            entry = entry.strip().lower().removesuffix(".")
            if entry.startswith("*."):
                self._suffix_hosts.append(entry[1:])  # keep the leading dot
            elif entry:
                self._exact_hosts.add(entry)
        self._allowed_origins = {o.strip().lower().rstrip("/") for o in allowed_origins if o}

    def _host_allowed(self, hostname: str) -> bool:
        if hostname == "localhost" or "." not in hostname or _is_ip_literal(hostname):
            return True
        if hostname in self._exact_hosts:
            return True
        return any(hostname.endswith(suffix) for suffix in self._suffix_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        host_header: str | None = None
        origin_header: str | None = None
        for key, value in scope.get("headers", ()):
            if key == b"host":
                host_header = value.decode("latin-1")
            elif key == b"origin":
                origin_header = value.decode("latin-1")

        hostname = _hostname_from_host_header(host_header) if host_header else None
        if hostname is None or not self._host_allowed(hostname):
            logger.warning("rejected request with disallowed Host header: %r", host_header)
            await _reject(scope, send, 400, "invalid host header")
            return

        if origin_header is not None:
            origin = origin_header.strip().lower().rstrip("/")
            if origin not in self._allowed_origins:
                logger.warning("rejected request with disallowed Origin header: %r", origin_header)
                await _reject(scope, send, 403, "cross-origin requests are not allowed")
                return

        await self.app(scope, receive, send)


async def _reject(scope: Scope, send: Send, status: int, detail: str) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
