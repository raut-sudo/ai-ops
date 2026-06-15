"""Routing edges for the LangGraph agent.

Topology:
  orchestrator → domain agents (+ optional memory_agent) → synthesizer
  → reflection → (retry fan-out OR action_executor OR response_composer)
  action_executor → response_composer
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


def _fan_out(state: AgentState, domains: list[str] | None = None) -> str | list[Send]:
    """Fan out to domain agents and optionally memory_agent.

    Args:
        state: Current agent state
        domains: Override domains to fan out to; if None, use intent.required_domains

    Returns:
        List of Send commands for parallel execution, or "response_composer" as fallback
    """
    intent = state["intent"]
    target_domains = domains or intent.required_domains
    retry_count = state.get("retry_count", 0)

    sends: list[Send] = [Send(f"{d}_agent", _worker_payload(state, d)) for d in target_domains]

    # Append memory_agent only on first pass (retry_count == 0)
    if intent.memory_needed and retry_count == 0:
        sends.append(Send("memory_agent", _memory_payload(state)))

    # Fallback: if no sends generated, route directly to response_composer
    if not sends:
        return "response_composer"

    return sends


def route_after_intent(state: AgentState) -> str | list[Send]:
    """Route after intent classification (orchestrator).

    Cases:
    - intent is None (orchestrator failed) → response_composer (error already set in state)
    - irrelevant → response_composer (skip investigation)
    - memory_recall → memory_agent (skip domain agents)
    - action_only → reflection (skip domain analysis; reflection proposes directly)
    - others → fan_out (parallel domain investigation)
    """
    intent = state.get("intent")

    # Orchestrator LLM failure — error is already set in state["error"]
    if intent is None:
        return "response_composer"

    if intent.intent_type == "irrelevant":
        return "response_composer"

    if intent.intent_type == "memory_recall":
        return "memory_agent"

    # No domains + no memory needed → response_composer (safe fallback)
    if not intent.required_domains and not intent.memory_needed:
        return "response_composer"

    return _fan_out(state)


def route_after_reflection(state: AgentState) -> str | list[Send]:
    """Route after reflection.

    - retry_with_domains + retries remain → targeted fan_out (loop back)
    - pass with action_requests → action_executor (HITL will be triggered)
    - pass without action_requests → response_composer (done)
    """
    result = state["reflection_result"]
    retry_count = state.get("retry_count", 0)
    intent = state.get("intent")

    # Retry path: verdict is retry AND budget not exhausted
    # retry_count was already incremented in reflection_node before this edge runs
    if result.verdict == "retry_with_domains" and retry_count < MAX_RETRIES:
        targets = result.domains_to_retry or (intent.required_domains if intent else [])
        if targets:
            return _fan_out(state, domains=targets)

    # Pass path: if domain agents raised action requests, route to action_executor for HITL
    action_requests = state.get("action_requests") or []
    if result.verdict == "pass" and action_requests:
        return "action_executor"

    # Default: no actions needed, go to response_composer
    return "response_composer"
