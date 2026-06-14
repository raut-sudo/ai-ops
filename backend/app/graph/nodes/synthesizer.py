"""Synthesizer node — agentic cross-domain correlation.

Uses create_agent (LangChain v1) with a ``get_domain_finding`` inspection tool
so the synthesizer can drill into any domain's full finding on demand, rather
than receiving a flat text dump it cannot re-query.

The structured response_format=SynthesisResult ensures the output is always
a validated schema — no JSON parsing brittle-ness.
"""

from __future__ import annotations

import asyncio
import json

import structlog
from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, Field

from app.config import settings
from app.graph.prompts import load_prompt
from app.schemas import DomainFinding, SynthesisResult

logger = structlog.get_logger(__name__)


class GetDomainFindingArgs(BaseModel):
    domain: str = Field(
        description="Domain name to inspect: sales, inventory, marketing, or support"
    )


def _make_get_domain_finding_tool(findings: dict[str, DomainFinding]) -> StructuredTool:
    """Build a closure tool that exposes the current invocation's findings."""

    async def _get_domain_finding(domain: str) -> str:
        """Return the full DomainFinding JSON for a specific domain."""
        finding = findings.get(domain)
        if finding is None:
            available = list(findings.keys())
            return json.dumps(
                {"error": f"No finding for domain '{domain}'. Available: {available}"}
            )
        return finding.model_dump_json(indent=2)

    return StructuredTool.from_function(
        coroutine=_get_domain_finding,
        name="get_domain_finding",
        description=(
            "Access the full DomainFinding for a specific domain agent's investigation. "
            "Use this to inspect the detailed findings, metrics, and anomalies for a "
            "domain when the initial summary is insufficient for root cause correlation."
        ),
        args_schema=GetDomainFindingArgs,
    )


def _build_context_message(
    query: str,
    findings: dict[str, DomainFinding],
    memory_context,
) -> str:
    """Assemble the user message with summaries of all available findings."""
    parts = [f"Query: {query}\n"]

    parts.append("Domain findings summary (use get_domain_finding for full details):")
    for domain, df in findings.items():
        parts.append(
            f"  [{domain}] severity={df.severity} confidence={df.confidence:.2f} "
            f"findings={len(df.findings)} anomalies={len(df.anomalies)}"
        )

    if memory_context and memory_context.past_incidents:
        parts.append(f"\nPast incidents: {len(memory_context.past_incidents)} relevant found.")
        for inc in memory_context.past_incidents[:2]:
            parts.append(f"  [{inc.occurred_at}] {inc.summary}")

    return "\n".join(parts)


SYNTHESIS_SYSTEM_PROMPT = """\
You are the synthesis layer of an e-commerce operations assistant.

You receive a summary of domain agent findings and the user's query.
Use the ``get_domain_finding`` tool whenever you need the full details for a
specific domain (its metrics, anomalies, and raw findings).

FIRST, classify the situation:
- If the query is a DIAGNOSTIC question (something is wrong: a drop, anomaly,
  stockout, complaint spike, "why did X happen") → produce root_causes that
  CORRELATE signals across domains. Explain how signals interact.

- If the query is a LOOKUP / REPORTING question (e.g., "what is the highest
  selling product", "what is the inventory status") → there is NO root cause.
  Set root_causes to an EMPTY list.
  Put the direct factual answer in correlated_explanation.
  Set confidence_score high (0.9+) if the findings clearly answer the question.

RULES:
- NEVER invent a root cause for a question that is just asking for information.
- correlated_explanation must DIRECTLY answer the user's actual question.
- Do NOT prefix it with boilerplate like "Correlated signals indicate..."
- Only mention domains that are RELEVANT to the query.
- Ignore domain findings with confidence < 0.2 (they are error signals).
- recommendations should be empty for pure lookups.
"""


async def synthesizer_node(state: dict) -> dict:
    """Synthesize domain findings + memory into a structured diagnosis.

    Uses create_agent so the synthesizer can call get_domain_finding to inspect
    any domain's full data before outputting a SynthesisResult.
    Falls back to a low-confidence result on error so reflection can retry.
    """
    findings: dict = state.get("domain_findings", {}) or {}
    query: str = state.get("query", "")
    memory_context = state.get("memory_context")

    if not findings:
        logger.warning("synthesizer_no_findings", query=query)
        return {
            "synthesis": SynthesisResult(
                correlated_explanation="No domain findings were available to synthesize.",
                root_causes=[],
                contributing_factors={},
                confidence_score=0.3,
                recommendations=[],
                domains_correlated=[],
            )
        }

    try:
        llm = AzureChatOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O,
            temperature=settings.AZURE_TEMPERATURE,
        )

        # Build the inspection tool with a closure over the current findings
        domain_tool = _make_get_domain_finding_tool(findings)
        context_msg = _build_context_message(query, findings, memory_context)

        agent = create_agent(
            llm,
            tools=[domain_tool],
            system_prompt=load_prompt("synthesizer_agent"),
            response_format=SynthesisResult,
        )

        result = await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [{"role": "user", "content": context_msg}]},
                config={"recursion_limit": 15},
            ),
            timeout=60.0,
        )

        synthesis: SynthesisResult = result["structured_response"]
        if synthesis is None:
            raise ValueError("structured_response was None")

        logger.info("synthesizer_success", confidence=synthesis.confidence_score)
        return {"synthesis": synthesis}

    except Exception as exc:
        logger.warning("synthesizer_failed", error=str(exc), exc_info=True)
        return {
            "synthesis": SynthesisResult(
                correlated_explanation="Synthesis could not be completed due to an LLM error.",
                root_causes=[],
                contributing_factors={},
                confidence_score=0.2,
                recommendations=[],
                domains_correlated=list(findings.keys()),
            )
        }
