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
        for item in df.findings:
            texts.append(f"[{domain}] {item}")

        for anomaly in df.anomalies:
            texts.append(f"[{domain}] anomaly: {anomaly}")

    return texts


SYNTHESIS_SYSTEM_PROMPT = """\
You are the synthesis layer of an e-commerce operations assistant.

You receive findings from domain agents (sales, inventory, marketing, support) and the user's query.

FIRST, classify the situation:
- If the query is a DIAGNOSTIC question (something is wrong: a drop, anomaly, stockout,
complaint spike, "why did X happen") → produce root_causes that CORRELATE signals across
domains, with evidence and confidence. Explain how signals interact.

- If the query is a LOOKUP / REPORTING question
(e.g., "what is the highest selling product", "what is the inventory status")
→ there is NO root cause.
Set root_causes to an EMPTY list.
Put the direct factual answer in correlated_explanation.
Set confidence_score high (0.9+) if the findings clearly answer the question.

RULES:
- NEVER invent a root cause for a question that is just asking for information.
- correlated_explanation must DIRECTLY answer the user's actual question in plain language.
Do NOT prefix it with boilerplate like "Correlated signals indicate multi-factor impact."
- Only mention domains that are RELEVANT to the query. Ignore irrelevant domain findings.
- If a domain finding says "agent error" or "unavailable", IGNORE it —
do not surface errors to the user and do not treat them as signals.
- recommendations should be empty for pure lookups.

Return structured output matching the schema.
"""


# Build prompt once
prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SYNTHESIS_SYSTEM_PROMPT),
        (
            "human",
            "Query: {query}\n\nDomain Findings:\n{findings}",
        ),
    ]
)


# Build model once
llm = AzureChatOpenAI(
    azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
    api_key=settings.AZURE_OPENAI_API_KEY,
    api_version=settings.AZURE_OPENAI_API_VERSION,
    azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O_MINI,
    temperature=0,
)


# Build chain once
# method="function_calling" avoids strict JSON-schema validation that rejects
# dict[str, str] (contributing_factors) in OpenAI's structured-output mode.
synthesis_chain = prompt | llm.with_structured_output(SynthesisResult, method="function_calling")


async def synthesizer_node(state: dict) -> dict:
    """Synthesize findings and memory into a structured diagnosis via LLM.

    If the LLM is unavailable or raises, returns a low-confidence SynthesisResult
    so the graph can continue (reflection will decide whether to retry or fail).
    """

    findings = state.get("domain_findings", {}) or {}
    query = state.get("query", "")

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
        findings_text = "\n".join(_findings_text(findings))

        result = await synthesis_chain.ainvoke(
            {
                "query": query,
                "findings": findings_text,
            }
        )

        logger.info("synthesizer_llm_success")

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
                confidence_score=0.2,
                recommendations=[],
                domains_correlated=list(findings.keys()),
            )
        }
