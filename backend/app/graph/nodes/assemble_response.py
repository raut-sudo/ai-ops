"""Assemble response node stub.

Pure assembly: builds FinalResponse from state (no I/O).
"""

from __future__ import annotations

from app.graph.state import AgentState
from app.schemas import FinalResponse


async def assemble_response_node(state: AgentState) -> dict:
    """Stub: assemble final response."""
    synthesis = state.get("synthesis")
    intent = state.get("intent") or {}

    return {
        "final_response": FinalResponse(
            session_id=state["session_id"],
            query=state["query"],
            intent_type=intent.intent_type if intent else "unknown",
            status="success",
            summary="Stub: investigation complete.",
            root_causes=synthesis.root_causes if synthesis else [],
            domain_findings=state.get("domain_findings", {}),
            memory_context=state.get("memory_context"),
            recommendations=[],
            proposed_actions=state.get("proposed_actions", []),
            executed_actions=state.get("action_results", []),
            confidence_score=0.5,
            thread_id=state["thread_id"],
            otel_trace_id=state["otel_trace_id"],
            langsmith_run_id=state.get("langsmith_run_id"),
        )
    }
