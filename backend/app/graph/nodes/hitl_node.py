"""HITL node stub.

Calls interrupt() to pause graph and request human approval.
Checkpointer saves state.
"""

from __future__ import annotations

from langgraph.types import interrupt

from app.schemas import HITLDecision


async def hitl_node(state: dict) -> dict:
    """Pause graph and request human decision via interrupt()."""
    proposals_payload = []
    for proposal in state.get("proposed_actions", []):
        if hasattr(proposal, "model_dump"):
            payload = proposal.model_dump()
            payload["action_type"] = proposal.action_type
            proposals_payload.append(payload)
        elif isinstance(proposal, dict):
            proposals_payload.append(proposal)

    synthesis = state.get("synthesis")
    summary = synthesis.correlated_explanation if synthesis else ""

    decision_raw = interrupt(
        {
            "proposed_actions": proposals_payload,
            "session_id": state.get("session_id"),
            "summary": summary,
        }
    )
    decision = HITLDecision.model_validate(decision_raw)
    return {"hitl_decision": decision}
