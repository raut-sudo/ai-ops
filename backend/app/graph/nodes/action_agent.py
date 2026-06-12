"""Action agent node stub.

LLM structured output: produces ActionProposal list.
Sole authority on action warranting.
"""

from __future__ import annotations


async def action_agent_node(state: dict) -> dict:
    """Stub: decide whether actions are warranted."""
    # Stub returns no proposals
    return {
        "proposed_actions": [],
    }
