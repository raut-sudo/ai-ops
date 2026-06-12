"""Memory retrieval node stub.

Deterministic node: embeds query, searches Qdrant, returns MemoryContext.
"""

from __future__ import annotations

from app.schemas import MemoryContext


async def memory_retrieve_node(state: dict) -> dict:
    """Stub: memory retrieval from Qdrant."""
    return {
        "memory_context": MemoryContext(
            past_incidents=[],
            recommended_actions_from_history=[],
            relevant_outcomes=[],
        )
    }
