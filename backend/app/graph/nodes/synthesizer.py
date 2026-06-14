"""Synthesizer node.

Correlates domain findings and memory context into a diagnosis.
Handles memory-recall intent when no domain findings are present.
"""

from __future__ import annotations

import importlib
import os

import structlog

from app.config import settings
from app.schemas import RootCause, SynthesisResult

logger = structlog.get_logger(__name__)


def _findings_text(findings: dict) -> list[str]:
    texts: list[str] = []
    for domain, df in findings.items():
        for item in df.findings:
            texts.append(f"[{domain}] {item}")
        for an in df.anomalies:
            texts.append(f"[{domain}] anomaly: {an}")
    return texts


def _derive_root_causes(findings: dict) -> list[RootCause]:
    causes: list[RootCause] = []

    inventory = findings.get("inventory")
    if inventory and any("out of stock" in a.lower() for a in inventory.anomalies):
        causes.append(
            RootCause(
                cause="Stockout of critical SKU reduced fulfillment capacity.",
                domain="inventory",
                evidence=inventory.findings + inventory.anomalies,
                confidence=min(0.95, max(0.7, inventory.confidence)),
            )
        )

    marketing = findings.get("marketing")
    if marketing and any("paused" in f.lower() for f in marketing.findings + marketing.anomalies):
        causes.append(
            RootCause(
                cause="Paused campaign reduced demand generation.",
                domain="marketing",
                evidence=marketing.findings + marketing.anomalies,
                confidence=min(0.93, max(0.68, marketing.confidence)),
            )
        )

    sales = findings.get("sales")
    if sales and not causes and sales.anomalies:
        causes.append(
            RootCause(
                cause="Sales contraction observed without a fully isolated driver.",
                domain="sales",
                evidence=sales.findings + sales.anomalies,
                confidence=min(0.85, max(0.55, sales.confidence)),
            )
        )

    support = findings.get("support")
    if support and any("sentiment" in a.lower() for a in support.anomalies):
        causes.append(
            RootCause(
                cause="Support sentiment deterioration suggests customer friction.",
                domain="support",
                evidence=support.findings + support.anomalies,
                confidence=min(0.82, max(0.55, support.confidence)),
            )
        )

    return causes


def _deterministic_synthesis(findings: dict, memory) -> SynthesisResult:
    root_causes = _derive_root_causes(findings)
    domains_correlated = list(findings.keys())

    clean_snippets = [
        s
        for s in _findings_text(findings)
        if "agent error" not in s.lower() and "unavailable" not in s.lower()
    ]

    if root_causes:
        explanation = (
            "Correlated signals indicate multi-factor impact across domains. "
            + " ".join(clean_snippets[:4])
        ).strip()
        confidence = min(0.93, sum(rc.confidence for rc in root_causes) / len(root_causes))
    elif clean_snippets:
        explanation = " ".join(clean_snippets[:4]).strip()
        confidence = 0.85
    else:
        explanation = "No strong domain signals were available to produce a confident answer."
        confidence = 0.5

    recommendations: list[str] = []
    if root_causes:
        if any(rc.domain == "inventory" for rc in root_causes):
            recommendations.append("Restock impacted SKU and monitor stockout recurrence.")
        if any(rc.domain == "marketing" for rc in root_causes):
            recommendations.append(
                "Re-activate or retune paused campaigns tied to the affected SKU."
            )
    if not recommendations and memory:
        recommendations.extend(memory.recommended_actions_from_history[:2])

    return SynthesisResult(
        correlated_explanation=explanation,
        root_causes=root_causes,
        contributing_factors={d: "signal detected" for d in domains_correlated},
        confidence_score=confidence,
        recommendations=recommendations,
        domains_correlated=domains_correlated,
    )


SYNTHESIS_SYSTEM_PROMPT = """\
You are the synthesis layer of an e-commerce operations assistant.

You receive findings from domain agents (sales, inventory, marketing, support) and the user's query.

FIRST, classify the situation:
- If the query is a DIAGNOSTIC question (something is wrong: a drop, anomaly, stockout, \
complaint spike, "why did X happen") → produce root_causes that CORRELATE signals across \
domains, with evidence and confidence. Explain how signals interact.
- If the query is a LOOKUP / REPORTING question (e.g., "what is the highest selling product", \
"what is the inventory status") → there is NO root cause. Set root_causes to an EMPTY list. \
Put the direct factual answer in correlated_explanation. Set confidence_score high (0.9+) \
if the findings clearly answer the question.

RULES:
- NEVER invent a root cause for a question that is just asking for information.
- correlated_explanation must DIRECTLY answer the user's actual question in plain language. \
Do NOT prefix it with boilerplate like "Correlated signals indicate multi-factor impact."
- Only mention domains that are RELEVANT to the query. Ignore irrelevant domain findings.
- If a domain finding says "agent error" or "unavailable", IGNORE it — do not surface errors \
to the user and do not treat them as signals.
- recommendations should be empty for pure lookups.

Return structured output matching the schema."""


async def _llm_synthesize(state: dict) -> SynthesisResult | None:
    query = state.get("query", "")
    findings = state.get("domain_findings", {}) or {}

    if not findings:
        return None

    try:
        AzureChatOpenAI = importlib.import_module("langchain_openai").AzureChatOpenAI
        ChatPromptTemplate = importlib.import_module("langchain_core.prompts").ChatPromptTemplate

        findings_text = "\n".join(_findings_text(findings))

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", SYNTHESIS_SYSTEM_PROMPT),
                ("user", "Query: {query}\n\nDomain Findings:\n{findings}"),
            ]
        )

        llm = AzureChatOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O,
            temperature=float(os.getenv("AZURE_TEMPRATURE", 0.8)),
        )
        structured_llm = llm.with_structured_output(SynthesisResult)
        messages = prompt.format_messages(query=query, findings=findings_text)
        result = await structured_llm.ainvoke(messages)
        logger.info("synthesizer_path", path="llm")
        return result
    except Exception as exc:
        logger.warning("synthesizer_llm_failed", error=str(exc), exc_info=True)
        return None


async def synthesizer_node(state: dict) -> dict:
    """Synthesize findings and memory into a structured diagnosis."""
    findings = state.get("domain_findings", {}) or {}
    memory = state.get("memory_context")

    # if intent and intent.intent_type == "memory_recall" and not findings:
    #     incidents = memory.past_incidents if memory else []
    #     if incidents:
    #         top = incidents[0]
    #         explanation = (
    #             f"Historical incident {top.incident_id} is the closest match: {top.summary}"
    #         )
    #         causes = [
    #             RootCause(
    #                 cause=c,
    #                 domain="memory",
    #                 evidence=[top.summary],
    #                 confidence=min(0.95, max(0.6, top.similarity_score)),
    #             )
    #             for c in top.root_causes[:3]
    #         ]
    #         recommendations = memory.recommended_actions_from_history[:5]
    #         confidence = min(0.92, max(0.6, top.similarity_score))
    #     else:
    #         explanation = "No relevant historical incident was retrieved for this query."
    #         causes = []
    #         recommendations = []
    #         confidence = 0.5

    #     return {
    #         "synthesis": SynthesisResult(
    #             correlated_explanation=explanation,
    #             root_causes=causes,
    #             contributing_factors={"memory": "memory_recall path"},
    #             confidence_score=confidence,
    #             recommendations=recommendations,
    #             domains_correlated=["memory"],
    #         )
    #     }

    result = await _llm_synthesize(state)
    if result is not None:
        return {"synthesis": result}

    logger.info("synthesizer_path", path="deterministic")
    return {"synthesis": _deterministic_synthesis(findings, memory)}
