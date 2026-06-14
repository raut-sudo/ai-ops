"""Sprint 6 idempotency tests — §24.2 mandatory (both required).

Blueprint FR-16, FR-17, NFR-12:
  Test 1 (FR-16): true-concurrent approves execute the action exactly once.
  Test 2 (FR-17): re-approve after completion returns 'skipped', no double write.
  Test 3 (NFR-12): Qdrant outage in aggregator does NOT fail the response.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.db.models import IncidentAction
from app.db.models import Session as SessionModel
from app.db.session import get_session
from app.graph.nodes.aggregator import aggregator_node
from app.graph.nodes.reflection import _execute_approved_actions
from app.schemas import (
    ActionProposal,
    HITLDecision,
    IntentClassification,
    RestockParams,
    RootCause,
    SynthesisResult,
)
from app.tools.inventory import get_stock_level

pytestmark = pytest.mark.usefixtures("ensure_seed_data")


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _seed_proposed_action(
    action_id: str,
    thread_id: str,
    qty: int,
) -> ActionProposal:
    """Insert an incident_actions row (status='proposed') and return the proposal.

    NOTE: incident_actions.session_id is FK → sessions.thread_id.
    """
    proposal = ActionProposal(
        action_id=action_id,
        target="SKU-101",
        parameters=RestockParams(sku="SKU-101", quantity=qty),
        risk_level="low",
        justification="idempotency test",
        estimated_impact="test",
    )
    async with get_session() as session:
        session.add(
            SessionModel(
                thread_id=thread_id,
                user_id="test-user",
                query="test",
                intent_type="direct_action",
                status="active",
            )
        )
        session.add(
            IncidentAction(
                action_id=action_id,
                session_id=thread_id,  # FK → sessions.thread_id
                action_type="restock_product",
                target="SKU-101",
                parameters=proposal.parameters.model_dump(),
                risk_level="low",
                status="proposed",
                justification="idempotency test",
            )
        )
        await session.commit()
    return proposal


def _exec_state(proposal: ActionProposal) -> dict:
    return {
        "user_id": "test-user",
        "proposed_actions": [proposal],
        "hitl_decision": HITLDecision(
            approved_action_ids=[proposal.action_id],
            rejected_action_ids=[],
            approver="test-user",
        ),
    }


async def _movement_count(action_id: str) -> int:
    async with get_session() as session:
        row = (
            await session.execute(
                text("""
                    SELECT COUNT(*) AS c
                    FROM inventory_movements
                    WHERE reference_id = :aid AND movement_type = 'restock'
                """),
                {"aid": action_id},
            )
        ).one()
    return int(row.c)


async def _action_status(action_id: str) -> str:
    async with get_session() as session:
        row = (
            await session.execute(
                text("SELECT status FROM incident_actions WHERE action_id = :aid"),
                {"aid": action_id},
            )
        ).one()
    return row.status


# ── Test 1 (FR-16): true-concurrent execute-once ─────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_approve_executes_action_exactly_once() -> None:
    """FR-16 §24.2: two simultaneous approve calls must execute exactly one restock.

    Uses asyncio.gather (true concurrent co-routine scheduling) to fire two
    _execute_approved_actions calls on the same action_id. The conditional
    UPDATE ... WHERE status='proposed' ensures only one wins.
    """
    action_id = str(uuid.uuid4())
    thread_id = f"concurrent-{uuid.uuid4()}"
    qty = 7

    proposal = await _seed_proposed_action(action_id, thread_id, qty)
    before = await get_stock_level("SKU-101")
    state = _exec_state(proposal)
    decision = state["hitl_decision"]
    proposals = state["proposed_actions"]

    # Fire two concurrent executions
    r1, r2 = await asyncio.gather(
        _execute_approved_actions(proposals, decision, state),
        _execute_approved_actions(proposals, decision, state),
        return_exceptions=True,
    )

    after = await get_stock_level("SKU-101")
    moves = await _movement_count(action_id)

    # One executed, one skipped
    statuses = {r[0].status for r in (r1, r2)}  # type: ignore[index]
    assert "executed" in statuses, "At least one call must have executed"
    assert "skipped" in statuses, "At least one call must have been skipped"

    # Stock increased by exactly qty — not 2x qty
    assert after["quantity_on_hand"] == before["quantity_on_hand"] + qty

    # Exactly one movement row
    assert moves == 1, f"Expected 1 movement row, got {moves}"

    # incident_actions is in final state
    final = await _action_status(action_id)
    assert final == "executed"

    # Teardown
    async with get_session() as session:
        await session.execute(
            text("UPDATE inventory SET quantity_on_hand = :qty WHERE sku = :sku"),
            {"qty": before["quantity_on_hand"], "sku": "SKU-101"},
        )
        await session.execute(
            text("DELETE FROM inventory_movements WHERE reference_id = :aid"),
            {"aid": action_id},
        )
        await session.commit()


# ── Test 2 (FR-17): re-approve after completion ───────────────────────────────


@pytest.mark.asyncio
async def test_reapprove_after_completion_is_idempotent() -> None:
    """FR-17 §24.2: re-approving an already-executed action must be a no-op.

    First execution: 'executed', stock increases.
    Second execution (same action_id, already 'executed' in DB): 'skipped', no change.
    """
    action_id = str(uuid.uuid4())
    thread_id = f"reapprove-{uuid.uuid4()}"
    qty = 6

    proposal = await _seed_proposed_action(action_id, thread_id, qty)
    before = await get_stock_level("SKU-101")
    state = _exec_state(proposal)
    decision = state["hitl_decision"]
    proposals = state["proposed_actions"]

    # ── First execution ──
    first = await _execute_approved_actions(proposals, decision, state)
    assert first[0].status == "executed"
    mid = await get_stock_level("SKU-101")
    assert mid["quantity_on_hand"] == before["quantity_on_hand"] + qty

    # ── Second execution (re-approve after completion) ──
    second = await _execute_approved_actions(proposals, decision, state)
    assert second[0].status == "skipped"
    assert second[0].result_payload["reason"] == "already_processed"

    # Stock must NOT have changed again
    after = await get_stock_level("SKU-101")
    assert after["quantity_on_hand"] == mid["quantity_on_hand"]

    # Exactly one movement row
    moves = await _movement_count(action_id)
    assert moves == 1, f"Expected 1 movement row after re-approve, got {moves}"

    # incident_actions stays 'executed'
    assert await _action_status(action_id) == "executed"

    # Teardown
    async with get_session() as session:
        await session.execute(
            text("UPDATE inventory SET quantity_on_hand = :qty WHERE sku = :sku"),
            {"qty": before["quantity_on_hand"], "sku": "SKU-101"},
        )
        await session.execute(
            text("DELETE FROM inventory_movements WHERE reference_id = :aid"),
            {"aid": action_id},
        )
        await session.commit()


# ── Test 3 (NFR-12): Qdrant outage must not fail aggregator ─────────────────


@pytest.mark.asyncio
async def test_aggregator_qdrant_outage_does_not_fail_response() -> None:
    """NFR-12 §30.6: Qdrant unavailable → aggregator returns final_response, never raises.

    The user-facing final_response must be unaffected by a vector-store outage.
    Only future memory recall degrades — the current response does not.
    """
    session_id = f"qdrant-outage-{uuid.uuid4()}"

    state = {
        "messages": [],
        "session_id": session_id,
        "query": "Why did sales drop?",
        "thread_id": "qdrant-outage-thread",
        "user_id": "test-user",
        "intent": IntentClassification(
            intent_type="business_diagnosis",
            required_domains=["inventory"],
            memory_needed=False,
            action_only=False,
            confidence=0.9,
            reasoning="Test.",
        ),
        "synthesis": SynthesisResult(
            correlated_explanation="SKU-101 stockout caused decline.",
            root_causes=[
                RootCause(
                    cause="Stockout",
                    domain="inventory",
                    evidence=["qty=0"],
                    confidence=0.9,
                )
            ],
            contributing_factors={},
            confidence_score=0.9,
            recommendations=[],
            domains_correlated=["inventory"],
        ),
        "proposed_actions": [],
        "action_results": [],
        "domain_findings": {},
        "memory_context": None,
        "reflection_result": None,
        "retry_count": 0,
        "hitl_decision": None,
        "final_response": None,
        "error": None,
        "otel_trace_id": "test-trace",
        "langsmith_run_id": None,
        "created_at": datetime.now(UTC),
    }

    # Qdrant raises — embed_text returns a valid vector but qdrant_upsert fails
    with (
        patch(
            "app.graph.nodes.aggregator.embed_text",
            new=AsyncMock(return_value=[0.0] * 1536),
        ),
        patch(
            "app.graph.nodes.aggregator.qdrant_upsert",
            new=AsyncMock(side_effect=Exception("Qdrant connection refused")),
        ),
    ):
        result = await aggregator_node(state)

    # Must return final_response without raising
    assert result["final_response"] is not None

    # Postgres incident row should still have been written
    async with get_session() as session:
        row = (
            await session.execute(
                text("SELECT summary, embedded FROM incidents WHERE id = :id"),
                {"id": session_id},
            )
        ).fetchone()

    assert row is not None, "incidents row must be written even when Qdrant fails"
    assert row.embedded is False  # not embedded because Qdrant failed


@pytest.mark.asyncio
async def test_aggregator_complete_outage_does_not_fail_response() -> None:
    """NFR-12: even a full Postgres + Qdrant outage in aggregator returns a final_response.

    This tests the outer exception handler that wraps both writes.
    """
    session_id = f"full-outage-{uuid.uuid4()}"

    state = {
        "messages": [],
        "session_id": session_id,
        "query": "Why did sales drop?",
        "thread_id": "full-outage-thread",
        "user_id": "test-user",
        "intent": IntentClassification(
            intent_type="business_diagnosis",
            required_domains=["inventory"],
            memory_needed=False,
            action_only=False,
            confidence=0.9,
            reasoning="Test.",
        ),
        "synthesis": SynthesisResult(
            correlated_explanation="SKU-101 stockout.",
            root_causes=[
                RootCause(
                    cause="Stockout",
                    domain="inventory",
                    evidence=["qty=0"],
                    confidence=0.9,
                )
            ],
            contributing_factors={},
            confidence_score=0.9,
            recommendations=[],
            domains_correlated=["inventory"],
        ),
        "proposed_actions": [],
        "action_results": [],
        "domain_findings": {},
        "memory_context": None,
        "reflection_result": None,
        "retry_count": 0,
        "hitl_decision": None,
        "final_response": None,
        "error": None,
        "otel_trace_id": "test-trace",
        "langsmith_run_id": None,
        "created_at": datetime.now(UTC),
    }

    # Patch get_session at the module level to simulate DB outage
    with patch(
        "app.graph.nodes.aggregator.get_session",
        side_effect=Exception("DB connection refused"),
    ):
        result = await aggregator_node(state)

    # Must return final_response without raising — user response is unaffected
    assert result["final_response"] is not None
