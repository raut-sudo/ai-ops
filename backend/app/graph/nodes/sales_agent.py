"""Sales agent node stub.

ReAct agent with sales tools. Returns DomainFinding for sales domain.
"""

from __future__ import annotations

from app.schemas import DomainFinding


async def sales_agent_node(state: dict) -> dict:
    """Stub: sales investigation via tools."""
    return {
        "domain_findings": {
            "sales": DomainFinding(
                domain="sales",
                findings=[],
                metrics=[],
                anomalies=[],
                confidence=0.5,
                tool_calls_made=[],
                severity="low",
            )
        }
    }
