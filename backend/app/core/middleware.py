"""HTTP middleware for the FastAPI application.

CorrelationIDMiddleware
  - Reads X-Correlation-ID from the incoming request header.
  - If absent, generates a fresh UUID4.
  - Binds it to structlog's contextvars so every log line in the request
    lifecycle carries it automatically.
  - Echoes it back on the response header.

AuthMiddleware (§19.2 auth stub)
  - Reads X-User-Id from the incoming request header.
  - If absent on a protected path, returns 401.
  - Sets request.state.user_id so every handler has a real user_id.
  - Exempt paths: /healthz, /health, /api/v1/health, /docs, /redoc, /openapi.json, /

NOTE: Both middlewares are implemented as pure ASGI callables (NOT BaseHTTPMiddleware).
BaseHTTPMiddleware wraps responses in an anyio cancel scope that cancels background
tasks — including LangGraph's astream generator — when the response body iteration
finishes. This causes spurious CancelledError on streaming endpoints (Starlette #1609).
Pure ASGI middleware has no cancel scope and does not interfere with streaming.

OTel middleware is NOT added here — FastAPIInstrumentor.instrument_app()
adds it automatically when setup_tracing() is called in lifespan.
"""

from __future__ import annotations

import json
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders, State
from starlette.types import ASGIApp, Receive, Scope, Send

# Paths that do NOT require X-User-Id (§19.2 — /healthz and its variants are open)
_AUTH_EXEMPT_PATHS = frozenset(
    {
        "/",
        "/healthz",
        "/health",
        "/api/v1/health",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)


class CorrelationIDMiddleware:
    """Pure ASGI middleware — generate or propagate X-Correlation-ID.

    Does NOT extend BaseHTTPMiddleware to avoid anyio cancel-scope
    interference with StreamingResponse (Starlette issue #1609).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        correlation_id: str = headers.get("x-correlation-id") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        async def send_with_header(message: dict) -> None:
            if message["type"] == "http.response.start":
                mutable = MutableHeaders(scope=message)
                mutable.append("X-Correlation-ID", correlation_id)
            await send(message)

        await self.app(scope, receive, send_with_header)


class AuthMiddleware:
    """Pure ASGI middleware — X-User-Id → request.state.user_id (§19.2 auth stub).

    Returns 401 JSON when X-User-Id is missing on protected endpoints.
    Does NOT extend BaseHTTPMiddleware to avoid anyio cancel-scope
    interference with StreamingResponse (Starlette issue #1609).
    Post-MVP: replace with JWT validation + RBAC (swap is localized here).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if path in _AUTH_EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        user_id: str | None = headers.get("x-user-id")
        if not user_id:
            body = json.dumps({"detail": "Missing X-User-Id header"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return

        # Inject user_id into scope state — accessible as request.state.user_id in handlers.
        # scope["state"] may already exist as a plain dict (some Starlette/Uvicorn versions
        # pre-initialize it as {}). Always ensure it is a State instance before setting attrs.
        existing = scope.get("state")
        if not isinstance(existing, State):
            scope["state"] = State()
        scope["state"].user_id = user_id
        await self.app(scope, receive, send)
