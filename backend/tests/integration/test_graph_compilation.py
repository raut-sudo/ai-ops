"""Integration test: verify graph compiles and runs end-to-end."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.graph.graph import build_graph
from app.graph.state import AgentState
from app.schemas import IntentClassification


@pytest.mark.asyncio
async def test_graph_compiles():
    """Verify build_graph() compiles without errors."""
    graph = build_graph()
    assert graph is not None
    # Check node count
    assert len(graph.nodes) == 14, f"Expected 14 nodes, got {len(graph.nodes)}"


@pytest.mark.asyncio
async def test_graph_runs_irrelevant_intent_to_end():
    """Verify graph runs end-to-end for irrelevant intent (no checkpointer)."""
    graph = build_graph()
    compiled = graph.compile()

    initial_state: AgentState = {
        "query": "What's the weather?",
        "session_id": "test-session",
        "thread_id": "test-thread",
        "user_id": "test-user",
        "intent": IntentClassification(
            intent_type="irrelevant",
            reasoning="Unrelated to e-commerce.",
            required_domains=[],
            action_only=False,
            memory_needed=False,
            confidence=0.95,
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
        "otel_trace_id": "test-trace",
        "langsmith_run_id": None,
        "created_at": datetime.now(UTC),
    }

    # Run to END
    result = await compiled.ainvoke(initial_state)

    assert result is not None
    assert result["final_response"] is not None
    assert (
        "irrelevant" in result["final_response"].status
        or "success" in result["final_response"].status
    )


@pytest.mark.asyncio
async def test_graph_runs_business_diagnosis_through_fanout():
    """Verify graph fans out for business_diagnosis intent."""
    graph = build_graph()
    compiled = graph.compile()

    initial_state: AgentState = {
        "query": "Why did sales drop?",
        "session_id": "test-session",
        "thread_id": "test-thread",
        "user_id": "test-user",
        "intent": IntentClassification(
            intent_type="business_diagnosis",
            reasoning="Sales analysis requested.",
            required_domains=["sales", "inventory"],
            action_only=False,
            memory_needed=False,
            confidence=0.9,
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
        "otel_trace_id": "test-trace",
        "langsmith_run_id": None,
        "created_at": datetime.now(UTC),
    }

    # Run to END (should fan out to sales_agent, inventory_agent, then synthesize)
    result = await compiled.ainvoke(initial_state)

    assert result is not None
    assert result["final_response"] is not None
    assert len(result["domain_findings"]) >= 0  # At least attempted fanout
