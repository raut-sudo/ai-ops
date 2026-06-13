"""Sprint 7 exit criteria: auth middleware tests (§19.2, §24.1).

Tests:
  - /healthz is open (no X-User-Id required).
  - /health is open.
  - /api/v1/health is open.
  - Protected endpoints return 401 when X-User-Id is absent.

No LLM, no graph, no DB needed — pure middleware/routing layer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from app.main import app

# ── shared ASGI client ─────────────────────────────────────────────────────


@pytest.fixture
async def client():
    """Async httpx client backed by the ASGI app (no real server)."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── helpers ────────────────────────────────────────────────────────────────


def _qdrant_mock(status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_instance = AsyncMock()
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    mock_instance.get = AsyncMock(return_value=mock_resp)
    return mock_instance


# ── open paths (no auth required) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_healthz_is_open_without_auth(client) -> None:
    """GET /healthz must return 200 with no X-User-Id header (§19.1)."""
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_liveness_is_open_without_auth(client) -> None:
    """GET /health (liveness) must return 200 with no X-User-Id header."""
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_v1_health_is_open_without_auth(client) -> None:
    """GET /api/v1/health (readiness) must be reachable without X-User-Id."""
    with patch("app.routers.health.httpx.AsyncClient", return_value=_qdrant_mock(200)):
        resp = await client.get("/api/v1/health")
    assert resp.status_code in {200, 503}  # degraded is ok; 401 is not


# ── protected paths return 401 when X-User-Id is absent ───────────────────


@pytest.mark.asyncio
async def test_chat_requires_x_user_id(client) -> None:
    """POST /api/v1/chat without X-User-Id → 401 (§19.2)."""
    resp = await client.post("/api/v1/chat", json={"query": "test"})
    assert resp.status_code == 401
    assert "X-User-Id" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_incidents_requires_x_user_id(client) -> None:
    """GET /api/v1/incidents without X-User-Id → 401."""
    resp = await client.get("/api/v1/incidents")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_actions_pending_requires_x_user_id(client) -> None:
    """GET /api/v1/actions/pending without X-User-Id → 401."""
    resp = await client.get("/api/v1/actions/pending")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_approve_requires_x_user_id(client) -> None:
    """POST /api/v1/approve without X-User-Id → 401."""
    resp = await client.post(
        "/api/v1/approve",
        json={
            "thread_id": "some-thread",
            "decision": {
                "approved_action_ids": [],
                "rejected_action_ids": [],
                "approver": "user-1",
            },
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_operational_stock_requires_x_user_id(client) -> None:
    """GET /api/v1/operational/stock/SKU-101 without X-User-Id → 401."""
    resp = await client.get("/api/v1/operational/stock/SKU-101")
    assert resp.status_code == 401


# ── X-User-Id present: middleware passes through ──────────────────────────


@pytest.mark.asyncio
async def test_chat_with_x_user_id_passes_auth(client) -> None:
    """POST /api/v1/chat with X-User-Id passes auth (may fail for other reasons)."""
    with (
        patch("app.routers.chat.get_compiled_graph") as mock_graph,
        patch("app.routers.chat.get_session") as mock_get_session,
    ):
        # Make session a no-op async context manager
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

        # Graph streams nothing; snapshot has no HITL
        mock_snapshot = MagicMock()
        mock_snapshot.next = ()
        mock_snapshot.values = {"final_response": None}

        mock_g = AsyncMock()
        mock_g.astream_events = AsyncMock(return_value=_async_iter([]))
        mock_g.aget_state = AsyncMock(return_value=mock_snapshot)
        mock_graph.return_value = mock_g

        resp = await client.post(
            "/api/v1/chat",
            json={"query": "test"},
            headers={"X-User-Id": "user-1"},
        )

    # Auth passed — response is 200 (streaming); body may be minimal.
    assert resp.status_code == 200


async def _async_iter(items):
    for item in items:
        yield item
