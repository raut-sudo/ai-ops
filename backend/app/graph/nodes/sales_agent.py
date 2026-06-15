"""Sales Agent — independent agentic node.

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

DOMAIN = "sales"
_TOOL_NAMES = {
    "analyze_sales",
    "get_top_products",
    "get_declining_products",
    "get_sales_distribution",
    # new
    "get_sales_by_sku",
    "get_customer_segment_breakdown",
    "get_orders_by_status",
    "get_campaign_revenue_attribution",
    "get_hourly_sales_trend",
    "get_product_details",
}


def _get_tools() -> list:
    return [t for t in READ_TOOLS if t.name in _TOOL_NAMES]


def _error_finding(reason: str) -> DomainFinding:
    return DomainFinding(
        domain=DOMAIN,
        findings=[f"Agent error: {reason}"],
        metrics=[],
        anomalies=[],
        status="error",
        tool_calls_made=[],
        severity="low",
    )


async def sales_agent_node(state: dict) -> dict:
    """Investigate using sales tools and return a DomainFinding."""
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
            system_prompt=load_prompt("sales_agent"),
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

        # Override LLM-self-reported status with grounded check.
        if not finding.tool_calls_made:
            finding.status = "error"
        elif not finding.findings and not finding.anomalies:
            finding.status = "partial"
        else:
            finding.status = "ok"

        logger.info("sales_agent_success", status=finding.status)

    except Exception as exc:
        logger.warning("sales_agent_error", error=str(exc), exc_info=True)
        finding = _error_finding(str(exc))

    return {"domain_findings": {DOMAIN: finding}}
