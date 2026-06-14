"""Inventory Agent — independent agentic node.

Uses create_agent (LangChain v1) with domain-specific tools and rich system
prompt. Returns a structured DomainFinding via response_format. No
deterministic fallback — if the LLM fails, an explicit low-confidence error
finding is returned so reflection can decide whether to retry.
"""

from __future__ import annotations

import asyncio

import structlog
from langchain.agents import create_agent
from langchain_openai import AzureChatOpenAI

from app.config import settings
from app.graph.prompts import load_prompt
from app.schemas import DomainFinding
from app.tools.adapters import READ_TOOLS

logger = structlog.get_logger(__name__)

DOMAIN = "inventory"
_TOOL_NAMES = {
    "analyze_inventory",
    "get_stock_level",
    "get_stockout_history",
    "get_inventory_turnover",
    "get_revenue_lost_to_stockouts",
}


def _get_tools() -> list:
    return [t for t in READ_TOOLS if t.name in _TOOL_NAMES]


def _error_finding(reason: str) -> DomainFinding:
    return DomainFinding(
        domain=DOMAIN,
        findings=[f"Agent error: {reason}"],
        metrics=[],
        anomalies=[],
        confidence=0.1,
        tool_calls_made=[],
        severity="low",
    )


async def inventory_agent_node(state: dict) -> dict:
    """Investigate using inventory tools and return a DomainFinding."""
    query = state.get("query", "")
    prior_messages = state.get("messages", [])

    try:
        llm = AzureChatOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O,
            temperature=settings.AZURE_TEMPERATURE,
        )

        agent = create_agent(
            llm,
            tools=_get_tools(),
            system_prompt=load_prompt("inventory_agent"),
            response_format=DomainFinding,
        )

        result = await asyncio.wait_for(
            agent.ainvoke(
                {
                    "messages": [
                        *prior_messages,
                        {"role": "user", "content": query},
                    ]
                },
                config={"recursion_limit": 25},
            ),
            timeout=90.0,
        )

        finding: DomainFinding = result["structured_response"]
        if finding is None:
            raise ValueError("structured_response was None")

        logger.info("inventory_agent_success", confidence=finding.confidence)

    except Exception as exc:
        logger.warning("inventory_agent_error", error=str(exc), exc_info=True)
        finding = _error_finding(str(exc))

    return {"domain_findings": {DOMAIN: finding}}
