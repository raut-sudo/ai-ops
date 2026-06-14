"""Integration tests for the Sprint 5 diagnostic pipeline.

§24.1 mandate: LLM calls are **mocked** — canned DomainFinding objects are
injected directly into domain_findings so tests are deterministic and require
no live Azure OpenAI credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.graph import build_graph
from app.schemas import (
    ActionProposal,
    DomainFinding,
    IntentClassification,
    MetricSnapshot,
    ReflectionResult,
    RestockParams,
    RootCause,
    SynthesisResult,
)

pytestmark = pytest.mark.usefixtures("ensure_seed_data")


# ── Canned DomainFindings (LLM mock — deterministic) ────────────────────────


def _canned_inventory_finding() -> DomainFinding:
    return DomainFinding(
        domain="inventory",
        findings=["SKU-101 quantity_on_hand=0, reorder_point=50."],
        metrics=[
            MetricSnapshot(name="quantity_on_hand", value=0, unit="units", period="now"),
            MetricSnapshot(name="reorder_point", value=50, unit="units", period="now"),
        ],
        anomalies=["SKU-101 is out of stock."],
        confidence=0.95,
        tool_calls_made=["get_stock_level"],
        severity="critical",
    )


def _canned_marketing_finding() -> DomainFinding:
    return DomainFinding(
        domain="marketing",
        findings=["1 paused campaign(s) detected."],
        metrics=[
            MetricSnapshot(name="campaign_count", value=3, unit="count", period="yesterday"),
            MetricSnapshot(name="paused_campaign_count", value=1, unit="count", period="yesterday"),
        ],
        anomalies=["Paused campaign state may suppress demand."],
        confidence=0.88,
        tool_calls_made=["get_campaign_performance"],
        severity="high",
    )


def _canned_sales_finding() -> DomainFinding:
    return DomainFinding(
        domain="sales",
        findings=["revenue declined 35.0% yesterday."],
        metrics=[
            MetricSnapshot(
                name="revenue", value=6500.0, unit="USD", period="yesterday", delta_pct=-35.0
            ),
        ],
        anomalies=["revenue materially below baseline."],
        confidence=0.9,
        tool_calls_made=["get_sales_metrics"],
        severity="high",
    )


# ── Mock builder ───────────────────────────────────────────────


def _mock_create_agent(structured_response) -> MagicMock:
    """Return a MagicMock replacing create_agent that yields a canned response."""
    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(return_value={"structured_response": structured_response})
    return MagicMock(return_value=mock_agent)


@pytest.mark.asyncio
async def test_golden_trace_reaches_reflection_with_two_root_causes() -> None:
    """Blueprint §24.1 / §13.4 Exit Criteria:

    - Diagnosis reaches reflection.
    - Synthesis contains ≥2 root causes: stockout (inventory) + paused campaign (marketing).
    - reflection_result.verdict == 'pass'.
    - proposed_actions populated (proves reflection generated action proposals).

    All LLM calls are mocked via create_agent patches — no Azure OpenAI credentials needed.
    interrupt() is mocked to reject all proposals so the graph continues to
    response_composer without a checkpointer.
    """

    graph = build_graph().compile()

    pre_set_intent = IntentClassification(
        intent_type="cross_domain_analysis",
        required_domains=["sales", "inventory", "marketing"],
        memory_needed=False,
        action_only=False,
        confidence=0.95,
        reasoning="Sales drop likely spans inventory and marketing.",
    )

    canned_synthesis = SynthesisResult(
        correlated_explanation=(
            "SKU-101 stockout cascade: zero inventory drove revenue decline; "
            "paused campaign removed demand signals."
        ),
        root_causes=[
            RootCause(
                cause="SKU-101 out of stock",
                domain="inventory",
                evidence=["qty=0"],
                confidence=0.95,
            ),
            RootCause(
                cause="Paused campaign suppressed demand",
                domain="marketing",
                evidence=["1 paused campaign"],
                confidence=0.88,
            ),
        ],
        contributing_factors={"inventory": "stockout", "marketing": "paused campaign"},
        confidence_score=0.95,
        recommendations=["Restock SKU-101", "Resume paused campaign"],
        domains_correlated=["inventory", "marketing", "sales"],
    )

    canned_reflection = ReflectionResult(
        verdict="pass",
        critique="Root causes confirmed across two domains.",
        confidence=0.95,
    )

    canned_proposals = [
        ActionProposal(
            action_id="diag-test-restock-sku101",
            target="SKU-101",
            parameters=RestockParams(sku="SKU-101", quantity=200),
            risk_level="low",
            justification="SKU-101 is out of stock",
            estimated_impact="Restore stock and recover revenue.",
        )
    ]

    initial_state = {
        "messages": [],
        "query": "Why did sales drop yesterday for SKU-101?",
        "session_id": "diag-session-001",
        "thread_id": "diag-thread-001",
        "user_id": "diag-user",
        "domain_findings": {},
        "memory_context": None,
        "synthesis": None,
        "reflection_result": None,
        "retry_count": 0,
        "proposed_actions": [],
        "hitl_decision": None,
        "action_results": [],
        "final_response": None,
        "error": None,
        "otel_trace_id": "trace-diag-001",
        "langsmith_run_id": None,
        "created_at": datetime.now(UTC),
    }

    with (
        patch("app.graph.nodes.orchestrator.create_agent", _mock_create_agent(pre_set_intent)),
        patch(
            "app.graph.nodes.sales_agent.create_agent", _mock_create_agent(_canned_sales_finding())
        ),
        patch(
            "app.graph.nodes.inventory_agent.create_agent",
            _mock_create_agent(_canned_inventory_finding()),
        ),
        patch(
            "app.graph.nodes.marketing_agent.create_agent",
            _mock_create_agent(_canned_marketing_finding()),
        ),
        patch("app.graph.nodes.synthesizer.create_agent", _mock_create_agent(canned_synthesis)),
        patch("app.graph.nodes.reflection._llm_reflect", AsyncMock(return_value=canned_reflection)),
        patch(
            "app.graph.nodes.reflection._llm_propose_actions",
            AsyncMock(return_value=canned_proposals),
        ),
        patch("app.graph.nodes.reflection._persist_proposed_actions", AsyncMock()),
        patch(
            "app.graph.nodes.reflection.interrupt",
            return_value={
                "approved_action_ids": [],
                "rejected_action_ids": ["diag-test-restock-sku101"],
                "approver": "test-auto-reject",
            },
        ),
        patch(
            "app.graph.nodes.response_composer._compose_summary",
            AsyncMock(return_value="SKU-101 stockout cascade resolved."),
        ),
        patch("app.graph.nodes.response_composer._persist_incident", AsyncMock()),
    ):
        await graph.ainvoke(initial_state)


# ── Retry-path tests (pure edge/node logic, no LLM) ─────────────────────────


@pytest.mark.asyncio
async def test_retry_path_targets_only_requested_domains() -> None:
    """Retry route sends only the domains_to_retry, not all original domains."""
    from app.graph import edges

    state = {
        "messages": [],
        "query": "Why did sales drop yesterday?",
        "session_id": "diag-session-002",
        "thread_id": "diag-thread-002",
        "user_id": "diag-user",
        "intent": IntentClassification(
            intent_type="business_diagnosis",
            required_domains=["sales", "inventory", "marketing"],
            memory_needed=False,
            action_only=False,
            confidence=0.9,
            reasoning="Needs diagnosis across domains.",
        ),
        "domain_findings": {},
        "memory_context": None,
        "synthesis": None,
        "reflection_result": ReflectionResult(
            verdict="retry_with_domains",
            critique="Need only inventory refresh.",
            confidence=0.6,
            domains_to_retry=["inventory"],
        ),
        "retry_count": 1,
        "proposed_actions": [],
        "hitl_decision": None,
        "action_results": [],
        "final_response": None,
        "error": None,
        "otel_trace_id": "trace-diag-002",
        "langsmith_run_id": None,
        "created_at": datetime.now(UTC),
    }

    route = edges.route_after_reflection(state)
    assert isinstance(route, list)
    node_names = [s.node for s in route]
    assert node_names == [
        "inventory_agent"
    ], f"Retry should target only inventory_agent, got: {node_names}"
    assert "sales_agent" not in node_names
    assert "marketing_agent" not in node_names


@pytest.mark.asyncio
async def test_retry_count_increments_on_reflection() -> None:
    """reflection_node always increments retry_count (§9.1, §30.5)."""
    from app.graph.nodes.reflection import reflection_node

    state = {
        "messages": [],
        "query": "Why did sales drop?",
        "session_id": "s",
        "thread_id": "t",
        "user_id": "u",
        "intent": IntentClassification(
            intent_type="business_diagnosis",
            required_domains=["sales"],
            memory_needed=False,
            action_only=False,
            confidence=0.9,
            reasoning="Diagnosis.",
        ),
        "domain_findings": {},
        "memory_context": None,
        "synthesis": None,
        "reflection_result": None,
        "retry_count": 1,
        "proposed_actions": [],
        "hitl_decision": None,
        "action_results": [],
        "final_response": None,
        "error": None,
        "otel_trace_id": "t",
        "langsmith_run_id": None,
        "created_at": datetime.now(UTC),
    }

    reflected = await reflection_node(state)
    assert (
        reflected["retry_count"] == 2
    ), f"Expected retry_count=2 after increment, got {reflected['retry_count']}"


@pytest.mark.asyncio
async def test_retry_capped_at_max_retries_routes_to_assemble() -> None:
    """When retry_count >= MAX_RETRIES, route goes to response_composer."""
    from app.graph import edges

    state = {
        "messages": [],
        "query": "Why did sales drop?",
        "session_id": "s",
        "thread_id": "t",
        "user_id": "u",
        "intent": IntentClassification(
            intent_type="business_diagnosis",
            required_domains=["sales"],
            memory_needed=False,
            action_only=False,
            confidence=0.9,
            reasoning="Diagnosis.",
        ),
        "domain_findings": {},
        "memory_context": None,
        "synthesis": None,
        "reflection_result": ReflectionResult(
            verdict="retry_with_domains",
            critique="Would retry but hit limit.",
            domains_to_retry=["sales"],
            confidence=0.6,
        ),
        "retry_count": edges.MAX_RETRIES,
        "proposed_actions": [],
        "hitl_decision": None,
        "action_results": [],
        "final_response": None,
        "error": None,
        "otel_trace_id": "t",
        "langsmith_run_id": None,
        "created_at": datetime.now(UTC),
    }

    route = edges.route_after_reflection(state)
    assert route == "response_composer", f"Expected response_composer when capped, got: {route}"
