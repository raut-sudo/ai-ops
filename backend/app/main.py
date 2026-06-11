"""AI E-Commerce Operations Brain — FastAPI application entry point.

The public surface is a single `app` object (created by `create_app()`).

Startup order:
  1. configure_logging — structlog + stdlib bridge
  2. setup_tracing     — OTel TracerProvider + FastAPI instrumentation
  3. mount middleware  — CorrelationIDMiddleware
  4. register handlers — AppException + catch-all 500

Shutdown order:
  1. shutdown_tracing  — flush BatchSpanProcessor
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.exceptions import AppException, app_exception_handler, unhandled_exception_handler
from app.core.logging import configure_logging
from app.core.middleware import CorrelationIDMiddleware
from app.observability.tracer import setup_tracing, shutdown_tracing
from app.routers.health import router as health_router

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup → yield → shutdown."""
    # ── Startup ──────────────────────────────────────────────────────────────
    configure_logging(log_level=settings.LOG_LEVEL, app_env=settings.APP_ENV)
    setup_tracing(app)
    log.info(
        "app.startup",
        service="ai-ops-backend",
        version=settings.APP_VERSION,
        env=settings.APP_ENV,
    )
    yield
    # ── Shutdown ─────────────────────────────────────────────────────────────
    shutdown_tracing()
    log.info("app.shutdown")


def create_app() -> FastAPI:
    """Application factory — returns a fully configured FastAPI instance."""
    application = FastAPI(
        title="AI E-Commerce Operations Brain",
        version=settings.APP_VERSION,
        description="LangGraph multi-agent e-commerce operations diagnosis system.",
        lifespan=lifespan,
        # Disable default /docs in production via env if desired; keep open for now.
        docs_url="/docs" if settings.APP_ENV != "production" else None,
        redoc_url="/redoc" if settings.APP_ENV != "production" else None,
    )

    # ── Middleware ────────────────────────────────────────────────────────────
    # NOTE: starlette adds middleware in reverse order, so first-added = outermost.
    application.add_middleware(CorrelationIDMiddleware)

    # ── Exception handlers ────────────────────────────────────────────────────
    application.add_exception_handler(AppException, app_exception_handler)  # type: ignore[arg-type]
    application.add_exception_handler(Exception, unhandled_exception_handler)

    # ── Routers ───────────────────────────────────────────────────────────────
    # Liveness (/health) is registered at root — no /api/v1 prefix.
    # Readiness (/api/v1/health) is declared with full path inside health_router.
    application.include_router(health_router)

    # Root liveness — kept for minimal container health checks.
    @application.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse({"service": "ai-ops-backend", "status": "ok"})

    return application


app = create_app()
