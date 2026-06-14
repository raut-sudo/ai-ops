"""Integration test: verify graph compiles and runs end-to-end."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.graph import build_graph
from app.graph.state import AgentState
from app.schemas import IntentClassification


def _mock_create_agent(structured_response) -> MagicMock:
    """Return a MagicMock replacing create_agent with a canned response."""
    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(return_value={"structured_response": structured_response})
    return MagicMock(return_value=mock_agent)


@pytest.mark.asyncio
async def test_graph_compiles():
    """Verify build_graph() compiles without errors."""
    graph = build_graph()
    assert graph is not None
    # 9 nodes: orchestrator, 4 domain agents, memory_agent, synthesizer, reflection, response_composer
    assert len(graph.nodes) == 9, f"Expected 9 nodes, got {len(graph.nodes)}"


@pytest.mark.asyncio
async def test_graph_runs_irrelevant_intent_to_end():
    """Verify graph runs end-to-end for irrelevant intent (no checkpointer).

    orchestrator returns irrelevant → response_composer → END
    LLM and DB calls are mocked so the test is deterministic.
    """
    irrelevant_intent = IntentClassification(
        intent_type="irrelevant",
        reasoning="Unrelated to e-commerce.",
        required_domains=[],
        action_only=False,
        memory_needed=False,
        confidence=0.95,
    )

    graph = build_graph()
    compiled = graph.compile()

    initial_state: AgentState = {
        "query": "What's the weather?",
        "session_id": "test-session",
        "thread_id": "test-thread",
        "user_id": "test-user",
        "messages": [],
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

    with (
        patch("app.graph.nodes.orchestrator.create_agent", _mock_create_agent(irrelevant_intent)),
        patch(
            "app.graph.nodes.response_composer._compose_summary",
            AsyncMock(return_value="Query is not relevant to e-commerce operations."),
        ),
        patch("app.graph.nodes.response_composer._persist_incident", AsyncMock()),
    ):
        result = await compiled.ainvoke(initial_state)

    assert result is not None
    assert result["final_response"] is not None
    assert result["final_response"].status == "irrelevant"


@pytest.mark.asyncio
async def test_graph_runs_irrelevant_second_query_to_end():
    """Verify graph runs end-to-end for a business query classified as irrelevant.

    orchestrator mocked to return irrelevant → response_composer → END
    LLM and DB calls are mocked so the test is deterministic.
    """
    irrelevant_intent = IntentClassification(
        intent_type="irrelevant",
        reasoning="Not an e-commerce question.",
        required_domains=[],
        action_only=False,
        memory_needed=False,
        confidence=0.99,
    )

    graph = build_graph()
    compiled = graph.compile()

    initial_state: AgentState = {
        "query": "Why did sales drop?",
        "session_id": "test-session-2",
        "thread_id": "test-thread-2",
        "user_id": "test-user",
        "messages": [],
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
        "otel_trace_id": "test-trace-2",
        "langsmith_run_id": None,
        "created_at": datetime.now(UTC),
    }

    with (
        patch("app.graph.nodes.orchestrator.create_agent", _mock_create_agent(irrelevant_intent)),
        patch(
            "app.graph.nodes.response_composer._compose_summary",
            AsyncMock(return_value="Query is not relevant to e-commerce operations."),
        ),
        patch("app.graph.nodes.response_composer._persist_incident", AsyncMock()),
    ):
        result = await compiled.ainvoke(initial_state)

    assert result is not None
    assert result["final_response"] is not None
    assert result["final_response"].status == "irrelevant"
