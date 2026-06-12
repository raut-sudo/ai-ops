"""Reflection node stub.

LLM structured output: produces ReflectionResult.
Intent-aware: memory_recall always passes.
Increments retry_count.
"""

from __future__ import annotations

from app.graph.state import AgentState
from app.schemas import ReflectionResult


async def reflection_node(state: AgentState) -> dict:
    """Stub: reflect on findings and decide verdict."""
    # Auto-pass for memory_recall (no retry)
    intent = state.get("intent")
    if intent and intent.intent_type == "memory_recall":
        verdict = "pass"
    else:
        verdict = "pass"  # stub always passes

    return {
        "reflection_result": ReflectionResult(
            verdict=verdict,
            critique="Stub reflection: no retry needed.",
            domains_to_retry=[],
            confidence=0.9,
        ),
        "retry_count": state.get("retry_count", 0) + 1,
    }
