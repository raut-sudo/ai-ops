from __future__ import annotations

from datetime import datetime
from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from app.schemas import (
    ActionProposal,
    ActionResult,
    DomainFinding,
    FinalResponse,
    HITLDecision,
    IntentClassification,
    MemoryContext,
    ReflectionResult,
    SynthesisResult,
)


def merge_domain_findings(
    existing: dict[str, DomainFinding],
    incoming: dict[str, DomainFinding],
) -> dict[str, DomainFinding]:
    """Merge domain findings by key; retried domains overwrite their own key only."""
    return {**(existing or {}), **(incoming or {})}


class AgentState(TypedDict):
    # ── Conversational memory (multi-turn, checkpointed) ──────────────────
    # AsyncPostgresSaver persists this across process restarts.
    messages: Annotated[list[AnyMessage], add_messages]

    # Input
    query: str
    session_id: str
    thread_id: str
    user_id: str

    # Routing
    intent: IntentClassification | None

    # Investigation
    domain_findings: Annotated[dict[str, DomainFinding], merge_domain_findings]

    # Memory
    memory_context: MemoryContext | None

    # Synthesis and reflection
    synthesis: SynthesisResult | None
    reflection_result: ReflectionResult | None
    retry_count: int

    # Actions
    proposed_actions: list[ActionProposal]
    hitl_decision: HITLDecision | None
    action_results: list[ActionResult]

    # Output
    final_response: FinalResponse | None

    # Metadata
    error: str | None
    otel_trace_id: str
    langsmith_run_id: str | None
    created_at: datetime
