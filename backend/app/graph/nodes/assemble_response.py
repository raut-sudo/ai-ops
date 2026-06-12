"""Assemble response node.

Pure assembly: builds FinalResponse from state (no I/O).
Blueprint §8, §13.4 — assemble_response is the only pure node; it must
never fail so the user always gets an answer.
"""

from __future__ import annotations

from app.graph.state import AgentState
from app.schemas import FinalResponse


async def assemble_response_node(state: AgentState) -> dict:
    """Build FinalResponse from graph state. Pure — no I/O, no side-effects."""
    synthesis = state.get("synthesis")
    intent = state.get("intent")
    action_results = state.get("action_results") or []
    proposed_actions = state.get("proposed_actions") or []

    # ── Confidence: prefer synthesis score, fall back to reflection ──
    confidence: float = 0.0
    if synthesis:
        confidence = synthesis.confidence_score
    elif state.get("reflection_result"):
        confidence = state["reflection_result"].confidence  # type: ignore[union-attr]

    # ── Status ──
    if state.get("error"):
        status = "error"
    elif intent and intent.intent_type == "irrelevant":
        status = "irrelevant"
    elif confidence < 0.5:
        status = "low_confidence"
    else:
        status = "success"

    # ── Summary: prefer synthesis explanation ──
    if synthesis:
        summary = synthesis.correlated_explanation
    elif intent and intent.intent_type == "irrelevant":
        summary = "Query is not relevant to e-commerce operations."
    elif intent and intent.intent_type == "memory_recall":
        mc = state.get("memory_context")
        if mc and mc.past_incidents:
            summary = f"Found {len(mc.past_incidents)} relevant past incident(s)."
        else:
            summary = "No relevant past incidents found."
    else:
        summary = "Investigation completed with limited findings."

    return {
        "final_response": FinalResponse(
            session_id=state["session_id"],
            query=state["query"],
            intent_type=intent.intent_type if intent else "unknown",
            status=status,
            summary=summary,
            root_causes=synthesis.root_causes if synthesis else [],
            domain_findings=state.get("domain_findings") or {},
            memory_context=state.get("memory_context"),
            recommendations=synthesis.recommendations if synthesis else [],
            proposed_actions=proposed_actions,
            executed_actions=action_results,
            confidence_score=confidence,
            low_confidence_flag=confidence < 0.5,
            thread_id=state["thread_id"],
            otel_trace_id=state.get("otel_trace_id", ""),
            langsmith_run_id=state.get("langsmith_run_id"),
        )
    }
