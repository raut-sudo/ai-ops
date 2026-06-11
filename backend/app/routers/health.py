"""Health check endpoints.

/health            — liveness probe (always 200, no external I/O)
/api/v1/health     — readiness probe (checks Qdrant; DB placeholder)

The router is mounted with prefix="" so both paths are explicit. The
/api/v1 prefix itself is added when this router is included in main.py.
"""

from __future__ import annotations

from typing import Literal

import httpx
import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import settings

log = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    app_env: str
    checks: dict[str, str]


@router.get("/health", response_model=HealthResponse, include_in_schema=False)
async def liveness() -> HealthResponse:
    """Liveness probe — always 200.  No external calls."""
    return HealthResponse(
        status="ok",
        service="ai-ops-backend",
        version=settings.APP_VERSION,
        app_env=settings.APP_ENV,
        checks={},
    )


@router.get(
    "/api/v1/health",
    response_model=HealthResponse,
    responses={503: {"description": "One or more dependencies degraded"}},
)
async def readiness() -> JSONResponse:
    """Readiness probe — pings Qdrant; DB check placeholder until Phase 2."""
    checks: dict[str, str] = {}
    degraded = False

    # --- Qdrant ---
    qdrant_url = f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}/healthz"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(qdrant_url)
        if resp.status_code == 200:
            checks["qdrant"] = "ok"
        else:
            checks["qdrant"] = f"http_{resp.status_code}"
            degraded = True
    except Exception as exc:
        log.warning("health.qdrant_unreachable", error=str(exc))
        checks["qdrant"] = "unreachable"
        degraded = True

    # --- Database (Phase 2 fills this in) ---
    checks["db"] = "not_configured"

    status: Literal["ok", "degraded"] = "degraded" if degraded else "ok"
    http_status = 503 if degraded else 200

    payload = HealthResponse(
        status=status,
        service="ai-ops-backend",
        version=settings.APP_VERSION,
        app_env=settings.APP_ENV,
        checks=checks,
    )
    return JSONResponse(status_code=http_status, content=payload.model_dump())
