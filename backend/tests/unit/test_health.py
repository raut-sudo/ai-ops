"""Unit tests for /health and /api/v1/health endpoints.

Uses httpx.AsyncClient with ASGITransport so no real server is started.
Qdrant calls are mocked with unittest.mock to avoid network I/O.

Key design note: the ASGI test client is created via a pytest fixture so it
is instantiated BEFORE any `patch()` context is entered.  If the client were
created inside a `with patch("...httpx.AsyncClient"):` block the mock would
also intercept the client constructor (same shared module object) and the
responses would be Mocks rather than real ASGI responses.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from app.core.exceptions import AppException
from app.main import app

# ── shared ASGI test client fixture ───────────────────────────────────────────


@pytest.fixture
async def client():
    """Async httpx client backed by the ASGI app (no real server)."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── helpers ────────────────────────────────────────────────────────────────────


def _qdrant_mock(status_code: int = 200):
    """Return a mock httpx.AsyncClient instance for patching the health router."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code

    mock_instance = AsyncMock()
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    mock_instance.get = AsyncMock(return_value=mock_resp)
    return mock_instance


# ── liveness ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_liveness_always_200(client) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "ai-ops-backend"


# ── readiness schema ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_readiness_schema_contains_required_fields(client) -> None:
    """GET /api/v1/health returns all HealthResponse fields regardless of status."""
    mock_instance = _qdrant_mock(200)

    with patch("app.routers.health.httpx.AsyncClient", return_value=mock_instance):
        resp = await client.get("/api/v1/health")

    body = resp.json()
    assert "status" in body
    assert "service" in body
    assert "version" in body
    assert "app_env" in body
    assert "checks" in body
    assert isinstance(body["checks"], dict)


# ── readiness — qdrant up ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_readiness_qdrant_ok_returns_200(client) -> None:
    mock_instance = _qdrant_mock(200)

    with patch("app.routers.health.httpx.AsyncClient", return_value=mock_instance):
        resp = await client.get("/api/v1/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["qdrant"] == "ok"
    assert body["checks"]["db"] == "not_configured"


# ── readiness — qdrant down ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_readiness_qdrant_down_returns_503_degraded(client) -> None:
    mock_instance = AsyncMock()
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    mock_instance.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with patch("app.routers.health.httpx.AsyncClient", return_value=mock_instance):
        resp = await client.get("/api/v1/health")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["qdrant"] == "unreachable"


# ── correlation-id header propagation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_correlation_id_header_set_on_response(client) -> None:
    """X-Correlation-ID must be present on every response."""
    resp = await client.get("/health")
    assert "x-correlation-id" in resp.headers


@pytest.mark.asyncio
async def test_correlation_id_is_echoed_when_sent(client) -> None:
    """If the client sends X-Correlation-ID it must be echoed back unchanged."""
    sent_id = "test-correlation-123"
    resp = await client.get("/health", headers={"X-Correlation-ID": sent_id})
    assert resp.headers.get("x-correlation-id") == sent_id


# ── AppException error envelope ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_app_exception_returns_typed_error_envelope() -> None:
    """An AppException must produce {"error": {"code", "message", "correlation_id"}}."""
    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/_test/raise")
    async def _raise() -> None:
        raise AppException(status_code=422, error_code="TEST_ERROR", message="test message")

    app.include_router(test_router)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/_test/raise", headers={"X-User-Id": "test-user"})

    assert resp.status_code == 422
    body = resp.json()
    assert "error" in body
    err = body["error"]
    assert err["code"] == "TEST_ERROR"
    assert err["message"] == "test message"
    assert "correlation_id" in err
