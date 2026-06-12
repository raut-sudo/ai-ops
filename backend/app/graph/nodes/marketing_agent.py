"""Marketing agent node stub.

ReAct agent with marketing tools. Returns DomainFinding for marketing domain.
"""

from __future__ import annotations

from app.schemas import DomainFinding


async def marketing_agent_node(state: dict) -> dict:
    """Stub: marketing investigation via tools."""
    return {
        "domain_findings": {
            "marketing": DomainFinding(
                domain="marketing",
                findings=["Stub: Marketing metrics retrieved."],
                metrics=[],
                anomalies=[],
                confidence=0.5,
                tool_calls_made=[],
                severity="low",
            )
        }
    }
