"""Routing edges for the LangGraph agent.

All routing functions build their decision based on state, not stub returns.
This ensures edges are testable independently of node implementations.
"""

from __future__ import annotations

from langgraph.types import Send

from app.config import settings
from app.graph.state import AgentState

MAX_RETRIES = settings.MAX_RETRIES


def _worker_payload(state: AgentState, domain: str) -> dict:
    """Worker input contract: minimal required keys.

    EXCLUDES domain_findings (reduced channel) to prevent circular data flow.
    INCLUDES session_id and user_id for logging/audit in workers.
    """
    return {
        "query": state["query"],
        "domain": domain,
        "thread_id": state["thread_id"],
        "session_id": state["session_id"],
        "user_id": state["user_id"],
        "retry_count": state.get("retry_count", 0),
    }


def _memory_payload(state: AgentState) -> dict:
    """Memory retrieval input contract: query + scoping keys."""
    return {
        "query": state["query"],
        "thread_id": state["thread_id"],
        "session_id": state["session_id"],
        "user_id": state["user_id"],
    }


def _fan_out(state: AgentState, domains: list[str] | None = None) -> list[Send]:
    """Fan out to domain agents and optionally memory_retrieve.

    Args:
        state: Current agent state
        domains: Override domains to fan out to; if None, use intent.required_domains

    Returns:
        List of Send commands for parallel execution
    """
    intent = state["intent"]
    target_domains = domains or intent.required_domains
    retry_count = state.get("retry_count", 0)

    sends: list[Send] = [Send(f"{d}_agent", _worker_payload(state, d)) for d in target_domains]

    # Append memory_retrieve only on first pass (retry_count == 0)
    if intent.memory_needed and retry_count == 0:
        sends.append(Send("memory_retrieve", _memory_payload(state)))

    # Fallback: if no sends generated, send to synthesizer via sales agent
    if not sends:
        return [Send("synthesizer", _worker_payload(state, "sales"))]

    return sends


def route_after_intent(state: AgentState) -> str | list[Send]:
    """Route after intent classification.

    Handles four cases:
    - irrelevant → assemble_response (skip investigation)
    - memory_recall → memory_retrieve (skip domain agents)
    - direct_action → action_agent (ask for action without diagnosis)
    - others → fan_out (parallel domain investigation)
    """
    intent = state["intent"]

    if intent.intent_type == "irrelevant":
        return "assemble_response"

    if intent.intent_type == "memory_recall":
        return "memory_retrieve"

    if intent.intent_type == "direct_action":
        return "action_agent"

    # No domains + no memory needed → assemble (shouldn't happen, but safe fallback)
    if not intent.required_domains and not intent.memory_needed:
        return "assemble_response"

    # Default: fan out to required domains
    return _fan_out(state)


def route_after_reflection(state: AgentState) -> str | list[Send]:
    """Route after reflection (retry decision point).

    Handles:
    - retry_with_domains + retry_count < MAX_RETRIES → targeted fan_out
    - actionable intent + root_causes → action_agent
    - others → assemble_response
    """
    result = state["reflection_result"]
    retry_count = state.get("retry_count", 0)
    intent = state["intent"]
    synth = state.get("synthesis")

    # ── Retry branch ──
    if result.verdict == "retry_with_domains" and retry_count < MAX_RETRIES:
        targets = result.domains_to_retry or intent.required_domains
        if not targets:
            return "assemble_response"
        return _fan_out(state, domains=targets)

    # ── Action branch (only if intent is actionable AND root causes exist) ──
    actionable_intents = {
        "business_diagnosis",
        "cross_domain_analysis",
        "inventory_check",
        "marketing_analysis",
        "support_analysis",
        "direct_action",
    }
    has_root_causes = bool(synth and synth.root_causes)
    if intent.intent_type in actionable_intents and has_root_causes:
        return "action_agent"

    # ── Default: assemble response ──
    return "assemble_response"


def route_after_action_agent(state: AgentState) -> str:
    """Route after action agent.

    - If proposals exist → hitl_node (pause for approval)
    - Otherwise → assemble_response (no actions needed)
    """
    return "hitl_node" if state.get("proposed_actions") else "assemble_response"


def route_after_hitl(state: AgentState) -> str:
    """Route after HITL decision.

    - If approved_action_ids exist → execute_actions
    - Otherwise → assemble_response (rejected or no approval)
    """
    decision = state.get("hitl_decision")
    return "execute_actions" if (decision and decision.approved_action_ids) else "assemble_response"
