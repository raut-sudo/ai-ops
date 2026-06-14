"""Integration tests for the Sprint 5 diagnostic pipeline.

§24.1 mandate: LLM calls are **mocked** — canned DomainFinding objects are
injected directly into domain_findings so tests are deterministic and require
no live Azure OpenAI credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.graph import build_graph
from app.schemas import (
    DomainFinding,
    IntentClassification,
    MetricSnapshot,
    ReflectionResult,
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


# ── Golden-trace test (LLM mocked via domain agent patches) ────────────────


def _make_mock_agent() -> AsyncMock:
    """Return an AsyncMock that maps domain → canned DomainFinding."""

    async def _side_effect(state: dict, domain: str) -> dict:
        canned = {
            "inventory": _canned_inventory_finding(),
            "marketing": _canned_marketing_finding(),
            "sales": _canned_sales_finding(),
        }
        finding = canned.get(domain)
        if finding:
            return {"domain_findings": {domain: finding}}
        return {"domain_findings": {}}

    return AsyncMock(side_effect=_side_effect)


@pytest.mark.asyncio
async def test_golden_trace_reaches_reflection_with_two_root_causes() -> None:
    """Blueprint §24.1 / §13.4 Exit Criteria:

    - Diagnosis reaches reflection.
    - Synthesis contains ≥2 root causes: stockout (inventory) + paused campaign (marketing).
    - reflection_result.verdict == 'pass'.
    - proposed_actions populated (proves reflection generated action proposals).

    Domain agents are mocked via patch so canned DomainFinding objects reach the
    synthesizer deterministically — no live DB or Azure OpenAI required.
    HITL interrupt() is mocked to reject all proposals (empty approved_action_ids)
    so the graph continues to aggregator without a checkpointer.
    """
    graph = build_graph().compile()

    initial_state = {
        "messages": [],
        "query": "Why did sales drop yesterday for SKU-101?",
        "session_id": "diag-session-001",
        "thread_id": "diag-thread-001",
        "user_id": "diag-user",
        "intent": IntentClassification(
            intent_type="cross_domain_analysis",
            required_domains=["sales", "inventory", "marketing"],
            memory_needed=False,
            action_only=False,
            confidence=0.95,
            reasoning="Sales drop likely spans inventory and marketing.",
        ),
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

    mock_agent = _make_mock_agent()
    # interrupt() is mocked to return a rejection dict so the graph
    # proceeds to aggregator without requiring a checkpointer.
    with (
        patch("app.graph.nodes.sales_agent.run_domain_react_agent", mock_agent),
        patch("app.graph.nodes.inventory_agent.run_domain_react_agent", mock_agent),
        patch("app.graph.nodes.marketing_agent.run_domain_react_agent", mock_agent),
        patch(
            "app.graph.nodes.reflection.interrupt",
            return_value={"approved_action_ids": [], "approver": "test-auto-reject"},
        ),
    ):
        result = await graph.ainvoke(initial_state)

    # ── Synthesis assertions ──────────────────────────────────────────────────
    synthesis = result["synthesis"]
    assert synthesis is not None, "Synthesizer must produce a SynthesisResult"
    assert (
        len(synthesis.root_causes) >= 2
    ), f"Expected ≥2 root causes, got {len(synthesis.root_causes)}"
    causes = [rc.cause.lower() for rc in synthesis.root_causes]
    assert any(
        "stockout" in c or "stock" in c for c in causes
    ), f"Expected a stockout root cause, got: {causes}"
    assert any(
        "campaign" in c or "paused" in c for c in causes
    ), f"Expected a paused campaign root cause, got: {causes}"

    # ── Reflection assertions ─────────────────────────────────────────────────
    reflection = result["reflection_result"]
    assert reflection is not None, "Reflection must produce a ReflectionResult"
    assert reflection.verdict == "pass", f"Expected verdict='pass', got '{reflection.verdict}'"

    # ── Reflection reached + action proposals generated ───────────────────────
    assert (
        result.get("proposed_actions") is not None
    ), "reflection must set proposed_actions (even if empty list)"
    assert (
        len(result["proposed_actions"]) >= 1
    ), "reflection must propose at least one action when two root causes are present."

    # ── Final response assembled ──────────────────────────────────────────────
    assert result["final_response"] is not None


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
    """When retry_count >= MAX_RETRIES, route goes to aggregator."""
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
    assert route == "aggregator", f"Expected aggregator when capped, got: {route}"
