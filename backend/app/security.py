"""HTTP hardening for a local developer tool that can write code, run tests and hold API keys.

* Host allow-list  — blocks DNS-rebinding (a web page pointing its own domain at 127.0.0.1)
* Origin check     — blocks cross-site requests (CSRF) to state-changing endpoints
* JSON-only writes — blocks "simple" cross-site form/text POSTs that skip CORS preflight
* Optional token   — MOBILEHEAL_API_TOKEN for deployments reachable from other machines
* Security headers — nosniff, no framing, strict referrer, CSP limiting where the page can connect
"""
from __future__ import annotations

import hmac
import os
from typing import Iterable, Set
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "10.0.2.2", "testserver"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob: https:; "
       "connect-src 'self' ws: wss:; frame-src https://www.figma.com https://embed.figma.com; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


def _host(value: str) -> str:
    value = (value or "").strip().lower()
    if value.startswith("["):                       # [::1]:8000
        return value[1:value.find("]")]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def allowed_hosts() -> Set[str]:
    extra = {h.strip().lower() for h in os.getenv("MOBILEHEAL_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return LOCAL_HOSTS | extra


class SecurityMiddleware:
    """Pure ASGI middleware (works for HTTP and WebSocket)."""

    def __init__(self, app, hosts: Iterable[str] = ()):
        self.app = app
        self.extra = set(hosts)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        hosts = allowed_hosts() | self.extra
        if "*" not in hosts and _host(headers.get("host", "")) not in hosts:
            return await self._deny(scope, receive, send, 421, "Host not allowed — add it to MOBILEHEAL_ALLOWED_HOSTS")

        origin = headers.get("origin")
        method = scope.get("method", "GET")
        if origin and origin != "null" and (scope["type"] == "websocket" or method in UNSAFE):
            if _host(urlsplit(origin).netloc) not in hosts and "*" not in hosts:
                return await self._deny(scope, receive, send, 403, "Cross-site request blocked")
        elif origin == "null" and method in UNSAFE:
            return await self._deny(scope, receive, send, 403, "Cross-site request blocked")

        path = scope.get("path", "")
        if scope["type"] == "http" and method in UNSAFE and path.startswith("/api/"):
            ctype = headers.get("content-type", "")
            length = headers.get("content-length", "0")
            if length not in ("", "0") and not ctype.startswith("application/json"):
                return await self._deny(scope, receive, send, 415, "Send JSON (Content-Type: application/json)")

        token = os.getenv("MOBILEHEAL_API_TOKEN", "")
        if token and path.startswith(("/api/", "/ws/")) and path != "/api/health":
            given = headers.get("x-mobileheal-token", "") or _cookie(headers.get("cookie", ""), "mh_token")
            if not hmac.compare_digest(given, token):
                return await self._deny(scope, receive, send, 401, "Missing or invalid MobileHeal API token")

        if scope["type"] == "websocket":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                h = list(message.get("headers", []))
                h += [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                      (b"referrer-policy", b"no-referrer"), (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                      (b"content-security-policy", CSP.encode())]
                if path.startswith("/api/"):
                    h.append((b"cache-control", b"no-store"))
                message["headers"] = h
            await send(message)

        return await self.app(scope, receive, send_wrapper)

    @staticmethod
    async def _deny(scope, receive, send, code, msg):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await JSONResponse({"detail": msg}, status_code=code)(scope, receive, send)


def _cookie(raw: str, name: str) -> str:
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""
