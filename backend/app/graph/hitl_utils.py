from __future__ import annotations


def _is_awaiting_hitl(snapshot: object) -> bool:
    """SOLE authority on whether a thread is paused at the HITL gate.

    HITL is now handled inside the reflection node via interrupt().
    The graph pauses with 'reflection' in next_nodes when awaiting human approval.

    Pinned to langgraph==1.2.4 semantics; re-verify on version bump.
    """
    next_nodes = getattr(snapshot, "next", ()) or ()
    return "reflection" in next_nodes
