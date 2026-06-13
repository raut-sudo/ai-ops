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

OTel middleware is NOT added here — FastAPIInstrumentor.instrument_app()
adds it automatically when setup_tracing() is called in lifespan.
"""

from __future__ import annotations

import json
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """Generate or propagate X-Correlation-ID; bind to structlog context."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        correlation_id: str = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        # Clear any stale bindings from a previous request served by this thread.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        response: Response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    """X-User-Id header → request.state.user_id (§19.2 auth stub).

    Returns 401 JSON when X-User-Id is missing on protected endpoints.
    Post-MVP: replace with JWT validation + RBAC (swap is localized here).
    """

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if request.url.path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        user_id = request.headers.get("X-User-Id")
        if not user_id:
            body = json.dumps({"detail": "Missing X-User-Id header"}).encode()
            return Response(
                content=body,
                status_code=401,
                media_type="application/json",
            )

        request.state.user_id = user_id
        return await call_next(request)
