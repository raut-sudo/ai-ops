"""Routing edges for the LangGraph agent.

Simplified to match the 6-node topology:
  intent_classifier → domain agents (+ optional memory_retrieve) → synthesizer
  → reflection → (retry fan-out OR aggregator)
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

    # Fallback: if no sends generated, route directly to synthesizer
    if not sends:
        return [Send("synthesizer", _worker_payload(state, "sales"))]

    return sends


def route_after_intent(state: AgentState) -> str | list[Send]:
    """Route after intent classification.

    Cases:
    - irrelevant → aggregator (skip investigation)
    - memory_recall → memory_retrieve (skip domain agents)
    - others → fan_out (parallel domain investigation)
    """
    intent = state["intent"]

    if intent.intent_type == "irrelevant":
        return "aggregator"

    if intent.intent_type == "memory_recall":
        return "memory_retrieve"

    # No domains + no memory needed → aggregator (safe fallback)
    if not intent.required_domains and not intent.memory_needed:
        return "aggregator"

    return _fan_out(state)


def route_after_reflection(state: AgentState) -> str | list[Send]:
    """Route after reflection.

    - retry_with_domains → targeted fan_out (if retries remain)
    - pass / fail → aggregator
    """
    result = state["reflection_result"]
    retry_count = state.get("retry_count", 0)
    intent = state.get("intent")

    if result.verdict == "retry_with_domains" and retry_count <= MAX_RETRIES:
        targets = result.domains_to_retry or (intent.required_domains if intent else [])
        if targets:
            return _fan_out(state, domains=targets)

    # pass, fail, or exhausted retries → aggregator
    return "aggregator"
