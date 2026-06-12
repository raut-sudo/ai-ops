"""Marketing ReAct domain worker node."""

from __future__ import annotations

from app.graph.nodes._react_domain import run_domain_react_agent


async def marketing_agent_node(state: dict) -> dict:
    """Use ReAct + marketing tools to produce DomainFinding."""
    return await run_domain_react_agent(state, "marketing")
