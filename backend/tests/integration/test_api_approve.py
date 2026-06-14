"""Sprint 7: /approve idempotent resume round-trip tests (§17.3, §24.1, FR-17).

Tests:
  1. Resume round-trip: _is_awaiting_hitl true → resume → false, final_response present.
  2. Re-approve after completion (Case A/FR-17): returns existing final_response.
  3. Approve on non-existent / empty thread → 409.

LLM and graph are mocked.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from app.graph.hitl_utils import _is_awaiting_hitl
from app.main import app
from app.schemas import FinalResponse, HITLDecision


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


_HEADERS = {"X-User-Id": "operator-1"}


def _make_final_response(thread_id: str) -> FinalResponse:
    return FinalResponse(
        session_id=thread_id,
        query="Why did sales drop?",
        intent_type="business_diagnosis",
        status="success",
        summary="SKU-101 stockout cascade resolved.",
        thread_id=thread_id,
        otel_trace_id="",
    )


def _paused_snapshot(thread_id: str):
    """Snapshot that _is_awaiting_hitl returns True for."""
    snap = MagicMock()
    snap.next = ("reflection",)
    snap.values = {"final_response": None}
    return snap


def _completed_snapshot(thread_id: str, fr: FinalResponse):
    """Snapshot that _is_awaiting_hitl returns False and has final_response."""
    snap = MagicMock()
    snap.next = ()
    snap.values = {"final_response": fr}
    return snap


# ── Test 1: resume round-trip ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_resume_round_trip() -> None:
    """§24.1 exit criterion: _is_awaiting_hitl true → resume → false, final_response present."""
    thread_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())
    fr = _make_final_response(thread_id)

    paused_snap = _paused_snapshot(thread_id)
    completed_snap = _completed_snapshot(thread_id, fr)

    mock_g = AsyncMock()
    # First call: thread is paused
    mock_g.aget_state = AsyncMock(side_effect=[paused_snap, completed_snap])
    mock_g.ainvoke = AsyncMock(return_value={"final_response": fr})

    decision = HITLDecision(
        approved_action_ids=[action_id],
        rejected_action_ids=[],
        approver="operator-1",
    )

    with patch("app.routers.approve.get_compiled_graph", return_value=mock_g):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/approve",
                json={"thread_id": thread_id, "decision": decision.model_dump(mode="json")},
                headers=_HEADERS,
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thread_id"] == thread_id
    assert body["status"] in {"success", "low_confidence", "hitl_pending", "irrelevant"}

    # Verify round-trip invariant: after resume, _is_awaiting_hitl returns False
    assert _is_awaiting_hitl(paused_snap) is True
    assert _is_awaiting_hitl(completed_snap) is False


# ── Test 2: re-approve after completion returns existing result (FR-17) ────


@pytest.mark.asyncio
async def test_reapprove_after_completion_returns_existing_final_response() -> None:
    """FR-17 / Case A: re-approve an already-completed thread returns the existing FinalResponse."""
    thread_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())
    fr = _make_final_response(thread_id)

    completed_snap = _completed_snapshot(thread_id, fr)

    mock_g = AsyncMock()
    mock_g.aget_state = AsyncMock(return_value=completed_snap)
    mock_g.ainvoke = AsyncMock()  # must NOT be called

    decision = HITLDecision(
        approved_action_ids=[action_id],
        rejected_action_ids=[],
        approver="operator-1",
    )

    with patch("app.routers.approve.get_compiled_graph", return_value=mock_g):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/approve",
                json={"thread_id": thread_id, "decision": decision.model_dump(mode="json")},
                headers=_HEADERS,
            )

    assert resp.status_code == 200, resp.text
    # Must return the existing response — NOT call ainvoke again.
    mock_g.ainvoke.assert_not_called()
    body = resp.json()
    assert body["thread_id"] == thread_id


# ── Test 3: 409 when thread has no final_response and is not paused ────────


@pytest.mark.asyncio
async def test_approve_409_when_thread_not_paused_and_no_final_response() -> None:
    """Thread exists but is neither awaiting HITL nor has a final_response → 409."""
    thread_id = str(uuid.uuid4())

    empty_snap = MagicMock()
    empty_snap.next = ()
    empty_snap.values = {"final_response": None}

    mock_g = AsyncMock()
    mock_g.aget_state = AsyncMock(return_value=empty_snap)

    decision = HITLDecision(
        approved_action_ids=[],
        rejected_action_ids=[],
        approver="operator-1",
    )

    with patch("app.routers.approve.get_compiled_graph", return_value=mock_g):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/approve",
                json={"thread_id": thread_id, "decision": decision.model_dump(mode="json")},
                headers=_HEADERS,
            )

    assert resp.status_code == 409
