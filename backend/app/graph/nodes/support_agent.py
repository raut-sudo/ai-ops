"""Support agent node stub.

ReAct agent with support tools. Returns DomainFinding for support domain.
"""

from __future__ import annotations

from app.schemas import DomainFinding


async def support_agent_node(state: dict) -> dict:
    """Stub: support investigation via tools."""
    return {
        "domain_findings": {
            "support": DomainFinding(
                domain="support",
                findings=["Stub: Support metrics retrieved."],
                metrics=[],
                anomalies=[],
                confidence=0.5,
                tool_calls_made=[],
                severity="low",
            )
        }
    }
