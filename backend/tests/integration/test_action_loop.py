"""Sprint 6 integration tests: full action loop.

Blueprint §14.5, §24.1 Exit Criteria:
- propose → approve → execute → operational write
- inventory_movements.reference_id == action_id linkage
- incident_actions.status flips proposed → executing → executed
- aggregator_node completes the loop (replaces assemble_response + persist_incident)

LLM is not invoked: state is pre-built with canned synthesis/proposals.
Postgres + Qdrant are live via Compose.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.db.models import IncidentAction
from app.db.models import Session as SessionModel
from app.db.session import get_session
from app.graph.nodes.aggregator import aggregator_node
from app.graph.nodes.reflection import _execute_approved_actions, _persist_proposed_actions
from app.schemas import (
    ActionProposal,
    ActionResult,
    DomainFinding,
    HITLDecision,
    IntentClassification,
    MemoryContext,
    MetricSnapshot,
    ReflectionResult,
    RestockParams,
    RootCause,
    SynthesisResult,
)
from app.tools.inventory import get_stock_level

pytestmark = pytest.mark.usefixtures("ensure_seed_data")


# ── Canned state builder ─────────────────────────────────────────────────────


def _make_full_state(
    thread_id: str,
    action_id: str,
    qty: int = 10,
    approved: bool = True,
) -> dict:
    """Build a complete AgentState-like dict for action-loop tests."""
    proposal = ActionProposal(
        action_id=action_id,
        target="SKU-101",
        parameters=RestockParams(sku="SKU-101", quantity=qty),
        risk_level="low",
        justification="SKU-101 out of stock; restock required.",
        estimated_impact="Restore stock availability and recover lost revenue.",
    )

    synthesis = SynthesisResult(
        correlated_explanation="SKU-101 stockout cascade: inventory zero caused revenue decline.",
        root_causes=[
            RootCause(
                cause="SKU-101 out of stock",
                domain="inventory",
                evidence=["SKU-101 quantity_on_hand=0", "OOS since yesterday 10:00"],
                confidence=0.95,
            )
        ],
        contributing_factors={"inventory": "stockout"},
        confidence_score=0.95,
        recommendations=["Restock SKU-101 immediately"],
        domains_correlated=["inventory"],
    )

    return {
        "messages": [],
        "query": "Why did sales drop yesterday?",
        "session_id": f"session-{thread_id}",
        "thread_id": thread_id,
        "user_id": "test-user",
        "intent": IntentClassification(
            intent_type="business_diagnosis",
            required_domains=["sales", "inventory"],
            memory_needed=True,
            action_only=False,
            confidence=0.95,
            reasoning="Sales drop likely driven by inventory stockout.",
        ),
        "domain_findings": {
            "inventory": DomainFinding(
                domain="inventory",
                findings=["SKU-101 quantity_on_hand=0, reorder_point=50."],
                metrics=[
                    MetricSnapshot(name="quantity_on_hand", value=0, unit="units", period="now")
                ],
                anomalies=["SKU-101 is out of stock."],
                confidence=0.95,
                tool_calls_made=["get_stock_level"],
                severity="critical",
            )
        },
        "memory_context": MemoryContext(
            past_incidents=[],
            recommended_actions_from_history=[],
            relevant_outcomes=[],
        ),
        "synthesis": synthesis,
        "reflection_result": ReflectionResult(
            verdict="pass",
            critique="Root causes confirmed; action recommended.",
            confidence=0.95,
        ),
        "retry_count": 1,
        "proposed_actions": [proposal],
        "hitl_decision": HITLDecision(
            approved_action_ids=[action_id] if approved else [],
            rejected_action_ids=[] if approved else [action_id],
            approver="test-user",
        ),
        "action_results": [],
        "final_response": None,
        "error": None,
        "otel_trace_id": "test-trace-action-loop",
        "langsmith_run_id": None,
        "created_at": datetime.now(UTC),
    }


async def _insert_proposed_action(action_id: str, thread_id: str, qty: int = 10) -> None:
    """Insert an incident_actions row with status='proposed'.

    NOTE: incident_actions.session_id is FK → sessions.thread_id.
    We use thread_id as session_id so the FK constraint is satisfied.
    """
    async with get_session() as session:
        session.add(
            SessionModel(
                thread_id=thread_id,
                user_id="test-user",
                query="Why did sales drop yesterday?",
                intent_type="business_diagnosis",
                status="active",
            )
        )
        session.add(
            IncidentAction(
                action_id=action_id,
                session_id=thread_id,  # FK → sessions.thread_id
                action_type="restock_product",
                target="SKU-101",
                parameters={
                    "action_type": "restock_product",
                    "sku": "SKU-101",
                    "quantity": qty,
                },
                risk_level="low",
                status="proposed",
                justification="SKU-101 out of stock.",
            )
        )
        await session.commit()


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_action_loop_writes_inventory_and_movement_row() -> None:
    """§14.5, §24.1: execute approved restock → inventory bumped + movement logged.

    The key proof:
    1. inventory.quantity_on_hand increases by exactly qty.
    2. inventory_movements row exists with reference_id == action_id.
    3. incident_actions.status == 'executed'.
    """
    action_id = str(uuid.uuid4())
    thread_id = f"loop-{uuid.uuid4()}"
    qty = 10

    before = await get_stock_level("SKU-101")
    await _insert_proposed_action(action_id, thread_id, qty)

    state = _make_full_state(thread_id, action_id, qty, approved=True)
    decision = state["hitl_decision"]
    proposals = state["proposed_actions"]

    # ── Execute via the internal helper now in reflection_node ──
    action_results = await _execute_approved_actions(proposals, decision, state)
    assert action_results[0].status == "executed"
    assert action_results[0].result_payload["sku"] == "SKU-101"

    # ── Verify: stock increased ──
    after = await get_stock_level("SKU-101")
    assert after["quantity_on_hand"] == before["quantity_on_hand"] + qty

    # ── Verify: reference_id linkage (§14.5 — the demo proof) ──
    async with get_session() as session:
        movement = (
            await session.execute(
                text("""
                    SELECT sku, movement_type, quantity_change, quantity_after
                    FROM inventory_movements
                    WHERE reference_id = :aid AND movement_type = 'restock'
                """),
                {"aid": action_id},
            )
        ).one()

        action_row = (
            await session.execute(
                text("SELECT status FROM incident_actions WHERE action_id = :aid"),
                {"aid": action_id},
            )
        ).one()

    assert movement.sku == "SKU-101"
    assert int(movement.quantity_change) == qty
    assert int(movement.quantity_after) == before["quantity_on_hand"] + qty
    assert action_row.status == "executed"

    # Teardown: restore seed state
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


@pytest.mark.asyncio
async def test_rejected_action_is_skipped_and_stock_unchanged() -> None:
    """§14.3: rejected actions produce 'skipped' result; Layer 1 is not written."""
    action_id = str(uuid.uuid4())
    thread_id = f"reject-{uuid.uuid4()}"
    qty = 15

    before = await get_stock_level("SKU-101")
    await _insert_proposed_action(action_id, thread_id, qty)

    state = _make_full_state(thread_id, action_id, qty, approved=False)
    decision = state["hitl_decision"]
    proposals = state["proposed_actions"]

    action_results = await _execute_approved_actions(proposals, decision, state)

    # Rejected → skipped
    assert action_results[0].status == "skipped"
    assert action_results[0].result_payload["reason"] == "rejected_by_human"

    # Stock must NOT change
    after = await get_stock_level("SKU-101")
    assert after["quantity_on_hand"] == before["quantity_on_hand"]


@pytest.mark.asyncio
async def test_persist_proposed_actions_writes_incident_actions_row() -> None:
    """§9.3: _persist_proposed_actions writes incident_actions rows with status='proposed'."""
    thread_id = f"agent-{uuid.uuid4()}"

    # Sessions row must exist first (session_id FK → sessions.thread_id)
    async with get_session() as session:
        session.add(
            SessionModel(
                thread_id=thread_id,
                user_id="test-user",
                query="Why did sales drop?",
                intent_type="business_diagnosis",
                status="active",
            )
        )
        await session.commit()

    action_id = str(uuid.uuid4())
    proposals = [
        ActionProposal(
            action_id=action_id,
            target="inventory",
            parameters=RestockParams(sku="SKU-101", quantity=100),
            risk_level="low",
            justification="SKU-101 out of stock",
            estimated_impact="Restore stock availability.",
        )
    ]
    state = {"thread_id": thread_id, "session_id": f"session-{thread_id}"}

    await _persist_proposed_actions(proposals, state)

    # Verify DB row was inserted
    async with get_session() as session:
        row = (
            await session.execute(
                text("SELECT status FROM incident_actions WHERE action_id = :aid"),
                {"aid": action_id},
            )
        ).fetchone()
        assert row is not None, f"incident_actions row missing for action_id={action_id}"
        assert row.status == "proposed"


@pytest.mark.asyncio
async def test_aggregator_builds_final_response_from_synthesis() -> None:
    """§8, §13.4: aggregator is pure and builds FinalResponse correctly."""
    action_id = str(uuid.uuid4())
    thread_id = f"assemble-{uuid.uuid4()}"
    qty = 10

    state = _make_full_state(thread_id, action_id, qty)
    # Simulate executed action already in results
    state["action_results"] = [
        ActionResult(
            action_id=action_id,
            status="executed",
            result_payload={"sku": "SKU-101", "new_quantity_on_hand": 200},
        )
    ]

    # Patch Qdrant so no real embedding needed (aggregator does best-effort persist)
    with (
        patch(
            "app.graph.nodes.aggregator.embed_text",
            new=AsyncMock(return_value=[0.0] * 1536),
        ),
        patch(
            "app.graph.nodes.aggregator.qdrant_upsert",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await aggregator_node(state)

    fr = result["final_response"]

    assert fr.status == "success"
    assert fr.confidence_score == pytest.approx(0.95, abs=0.01)
    assert fr.session_id == f"session-{thread_id}"
    assert len(fr.root_causes) == 1
    assert (
        "stockout" in fr.root_causes[0].cause.lower()
        or "out of stock" in fr.root_causes[0].cause.lower()
    )
    assert fr.executed_actions[0].status == "executed"
    assert not fr.low_confidence_flag


@pytest.mark.asyncio
async def test_aggregator_persists_incident_to_postgres() -> None:
    """§15.3: aggregator inserts an incidents row for diagnostic intents."""
    session_id = f"persist-test-{uuid.uuid4()}"

    state = {
        "messages": [],
        "session_id": session_id,
        "query": "Why did sales drop?",
        "thread_id": "persist-thread",
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
            correlated_explanation="SKU-101 stockout caused the drop.",
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

    # Patch Qdrant so no real embedding needed
    with (
        patch(
            "app.graph.nodes.aggregator.embed_text",
            new=AsyncMock(return_value=[0.0] * 1536),
        ),
        patch(
            "app.graph.nodes.aggregator.qdrant_upsert",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await aggregator_node(state)

    assert result["final_response"] is not None  # always returns a final_response

    # Verify Postgres row
    async with get_session() as session:
        row = (
            await session.execute(
                text("SELECT summary, root_causes FROM incidents WHERE id = :id"),
                {"id": session_id},
            )
        ).fetchone()

    assert row is not None
    assert "stockout" in row.summary.lower() or "sku-101" in row.summary.lower()
    assert len(row.root_causes) >= 1


@pytest.mark.asyncio
async def test_aggregator_skips_persist_for_memory_recall_intent() -> None:
    """FR-15: aggregator must NOT write incidents for memory_recall intent."""
    session_id = f"memory-recall-{uuid.uuid4()}"

    state = {
        "messages": [],
        "session_id": session_id,
        "query": "Have we seen this before?",
        "thread_id": "memory-recall-thread",
        "user_id": "test-user",
        "intent": IntentClassification(
            intent_type="memory_recall",
            required_domains=[],
            memory_needed=True,
            action_only=False,
            confidence=0.9,
            reasoning="User asked about past incidents.",
        ),
        "synthesis": SynthesisResult(
            correlated_explanation="Past incidents found.",
            root_causes=[
                RootCause(cause="prior stockout", domain="inventory", evidence=[], confidence=0.8)
            ],
            contributing_factors={},
            confidence_score=0.8,
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

    result = await aggregator_node(state)
    assert result["final_response"] is not None

    # Should NOT have written to DB (memory_recall is not a diagnostic intent)
    async with get_session() as session:
        row = (
            await session.execute(
                text("SELECT id FROM incidents WHERE id = :id"),
                {"id": session_id},
            )
        ).fetchone()

    assert row is None
