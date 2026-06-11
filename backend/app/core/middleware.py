"""HTTP middleware for the FastAPI application.

CorrelationIDMiddleware
  - Reads X-Correlation-ID from the incoming request header.
  - If absent, generates a fresh UUID4.
  - Binds it to structlog's contextvars so every log line in the request
    lifecycle carries it automatically.
  - Echoes it back on the response header.

OTel middleware is NOT added here — FastAPIInstrumentor.instrument_app()
adds it automatically when setup_tracing() is called in lifespan.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


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
