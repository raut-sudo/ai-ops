"""Synthesizer node.

Correlates domain findings and memory context into a diagnosis via LLM.
No deterministic fallback — all root cause reasoning is LLM-driven.
"""

from __future__ import annotations

import structlog
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import AzureChatOpenAI

from app.config import settings
from app.schemas import SynthesisResult

logger = structlog.get_logger(__name__)


def _findings_text(findings: dict) -> list[str]:
    texts: list[str] = []

    for domain, df in findings.items():
        if df.status == "error":
            continue  # Skip failed agents entirely
        for item in df.findings:
            texts.append(f"[{domain}] {item}")

        for anomaly in df.anomalies:
            texts.append(f"[{domain}] anomaly: {anomaly}")

    return texts


SYNTHESIS_SYSTEM_PROMPT = """\
You are the synthesis layer of an e-commerce operations assistant.

You receive findings from domain agents (sales, inventory, marketing, support) and the user's query.

## Classify the situation

If the query is DIAGNOSTIC (something is wrong: a drop, anomaly, stockout, complaint
spike, "why did X happen") → produce root_causes that CORRELATE signals across domains
with concrete evidence. Explain how signals interact.

If the query is LOOKUP / REPORTING (e.g., "what is the highest selling product",
"current inventory status") → there is NO root cause. Set root_causes to []. Put the
direct factual answer in correlated_explanation.

## Set status

- "answered"     → findings contain concrete data that directly addresses the query
- "partial"      → findings contain some relevant data but not enough for a confident answer
- "insufficient" → findings do not address the query at all

## Rules
- NEVER invent a root cause for a question that is just asking for information.
- correlated_explanation must DIRECTLY answer the user's actual question in plain language.
- Only mention domains that are RELEVANT to the query.
- Ignore domain findings whose status is "error".
- recommendations should be empty for pure lookups.
"""


# Build prompt once (no LLM instantiation at module level)
_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SYNTHESIS_SYSTEM_PROMPT),
        (
            "human",
            "Query: {query}\n\nDomain Findings:\n{findings}",
        ),
    ]
)


_synthesis_chain = None


def _get_synthesis_chain():
    """Return the synthesis chain, constructing it lazily on first call.

    This avoids crashing on import when AZURE_OPENAI_ENDPOINT is not set.
    """
    global _synthesis_chain
    if _synthesis_chain is None:
        llm = AzureChatOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O_MINI,
            temperature=0,
        )
        # method="function_calling" avoids strict JSON-schema validation that rejects
        # dict[str, str] (contributing_factors) in OpenAI's structured-output mode.
        _synthesis_chain = _prompt | llm.with_structured_output(
            SynthesisResult, method="function_calling"
        )
    return _synthesis_chain


async def synthesizer_node(state: dict) -> dict:
    """Synthesize findings and memory into a structured diagnosis via LLM.

    If the LLM is unavailable or raises, returns a low-confidence SynthesisResult
    so the graph can continue (reflection will decide whether to retry or fail).
    """

    findings = state.get("domain_findings", {}) or {}
    query = state.get("query", "")
    action_requests = state.get("action_requests", []) or []

    if not findings:
        logger.warning("synthesizer_no_findings", query=query)

        return {
            "synthesis": SynthesisResult(
                correlated_explanation="No domain findings were available to synthesize.",
                root_causes=[],
                contributing_factors={},
                status="insufficient",
                recommendations=[],
                domains_correlated=[],
                recommended_actions=action_requests,
            )
        }

    try:
        synthesis_chain = _get_synthesis_chain()
        findings_text = "\n".join(_findings_text(findings))

        result = await synthesis_chain.ainvoke(
            {
                "query": query,
                "findings": findings_text,
            }
        )

        # Pass through action requests raised by domain agents
        result.recommended_actions = action_requests

        logger.info("synthesizer_llm_success", action_requests=len(action_requests))

        return {
            "synthesis": result,
        }

    except Exception as exc:
        logger.warning(
            "synthesizer_llm_failed",
            error=str(exc),
            exc_info=True,
        )

        return {
            "synthesis": SynthesisResult(
                correlated_explanation="Synthesis could not be completed due to an LLM error.",
                root_causes=[],
                contributing_factors={},
                status="insufficient",
                recommendations=[],
                domains_correlated=list(findings.keys()),
                recommended_actions=action_requests,
            )
        }
