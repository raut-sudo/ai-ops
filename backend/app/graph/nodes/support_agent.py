"""Support Agent — independent agentic node.

Uses create_agent (LangChain v1) with domain-specific tools and rich system
prompt. Returns a structured DomainFinding via response_format.
Also surfaces ActionRequest objects for any permissioned tools called
(request_support_ticket, request_alert).
"""

from __future__ import annotations

import asyncio
import json

import structlog
from langchain.agents import create_agent
from langchain_core.messages import ToolMessage
from langchain_openai import AzureChatOpenAI

from app.config import settings
from app.graph.prompts import load_prompt
from app.schemas import ActionRequest, DomainFinding
from app.tools.adapters import PERMISSIONED_TOOL_NAMES, READ_TOOLS, REQUEST_TOOLS

logger = structlog.get_logger(__name__)

DOMAIN = "support"
_READ_TOOL_NAMES = {
    "analyze_support",
    "get_products_with_high_complaint_rate",
    "get_common_complaint_categories",
    "get_common_return_reasons",
    "get_churn_risk_products",
    # new
    "get_ticket_resolution_times",
    "get_open_tickets_snapshot",
    "get_sentiment_trend",
    "get_return_rate_by_sku",
    "get_product_details",
}
_REQUEST_TOOL_NAMES = {"request_support_ticket", "request_alert"}


def _get_tools() -> list:
    read = [t for t in READ_TOOLS if t.name in _READ_TOOL_NAMES]
    request = [t for t in REQUEST_TOOLS if t.name in _REQUEST_TOOL_NAMES]
    return [*read, *request]


def _extract_action_requests(messages: list) -> list[ActionRequest]:
    requests: list[ActionRequest] = []
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name in PERMISSIONED_TOOL_NAMES:
            try:
                data = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                requests.append(ActionRequest.model_validate(data))
            except Exception as exc:
                logger.warning("action_request_parse_failed", error=str(exc), tool=msg.name)
    return requests


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


async def support_agent_node(state: dict) -> dict:
    """Investigate using support tools and return a DomainFinding + any ActionRequests."""
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
            system_prompt=load_prompt("support_agent"),
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

        if not finding.tool_calls_made:
            finding.status = "error"
        elif not finding.findings and not finding.anomalies:
            finding.status = "partial"
        else:
            finding.status = "ok"

        action_requests = _extract_action_requests(result.get("messages", []))
        logger.info(
            "support_agent_success",
            status=finding.status,
            action_requests=len(action_requests),
        )

    except Exception as exc:
        logger.warning("support_agent_error", error=str(exc), exc_info=True)
        finding = _error_finding(str(exc))
        action_requests = []

    output: dict = {"domain_findings": {DOMAIN: finding}}
    if action_requests:
        output["action_requests"] = action_requests
    return output
