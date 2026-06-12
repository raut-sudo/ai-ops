"""Inventory ReAct domain worker node."""

from __future__ import annotations

from app.graph.nodes._react_domain import run_domain_react_agent


async def inventory_agent_node(state: dict) -> dict:
    """Use ReAct + inventory tools to produce DomainFinding."""
    return await run_domain_react_agent(state, "inventory")
