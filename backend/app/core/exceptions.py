"""Typed application exceptions and FastAPI exception handlers.

All API error responses use the same envelope:
  {"error": {"code": str, "message": str, "correlation_id": str}}

Register both handlers on the FastAPI app instance inside create_app().
"""

from __future__ import annotations

import structlog
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

log = structlog.get_logger(__name__)


class AppException(HTTPException):
    """Base application exception.

    Carries a machine-readable error_code alongside the HTTP status so
    clients can branch on code without parsing message strings.
    """

    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.error_code = error_code
        self.message = message


def _error_body(code: str, message: str, correlation_id: str) -> dict:
    return {"error": {"code": code, "message": message, "correlation_id": correlation_id}}


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Handler for AppException — logs a WARNING, returns the typed error body."""
    correlation_id: str = request.headers.get("X-Correlation-ID", "")
    log.warning(
        "app_exception",
        error_code=exc.error_code,
        message=exc.message,
        status_code=exc.status_code,
        path=str(request.url),
        correlation_id=correlation_id,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.error_code, exc.message, correlation_id),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unhandled exceptions — logs full traceback, returns 500."""
    correlation_id: str = request.headers.get("X-Correlation-ID", "")
    log.exception(
        "unhandled_exception",
        exc_info=exc,
        path=str(request.url),
        correlation_id=correlation_id,
    )
    return JSONResponse(
        status_code=500,
        content=_error_body(
            "INTERNAL_ERROR",
            "An unexpected error occurred.",
            correlation_id,
        ),
    )
