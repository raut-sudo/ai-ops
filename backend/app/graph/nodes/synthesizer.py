"""Synthesizer node.

Correlates domain findings and memory context into a diagnosis.
Handles memory-recall intent when no domain findings are present.
"""

from __future__ import annotations

from app.schemas import RootCause, SynthesisResult


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


async def synthesizer_node(state: dict) -> dict:
    """Synthesize findings and memory into a structured diagnosis."""
    intent = state.get("intent")
    findings = state.get("domain_findings", {}) or {}
    memory = state.get("memory_context")

    if intent and intent.intent_type == "memory_recall" and not findings:
        incidents = memory.past_incidents if memory else []
        if incidents:
            top = incidents[0]
            explanation = (
                f"Historical incident {top.incident_id} is the closest match: {top.summary}"
            )
            causes = [
                RootCause(
                    cause=c,
                    domain="memory",
                    evidence=[top.summary],
                    confidence=min(0.95, max(0.6, top.similarity_score)),
                )
                for c in top.root_causes[:3]
            ]
            recommendations = memory.recommended_actions_from_history[:5]
            confidence = min(0.92, max(0.6, top.similarity_score))
        else:
            explanation = "No relevant historical incident was retrieved for this query."
            causes = []
            recommendations = []
            confidence = 0.5

        return {
            "synthesis": SynthesisResult(
                correlated_explanation=explanation,
                root_causes=causes,
                contributing_factors={"memory": "memory_recall path"},
                confidence_score=confidence,
                recommendations=recommendations,
                domains_correlated=["memory"],
            )
        }

    root_causes = _derive_root_causes(findings)
    domains_correlated = list(findings.keys())
    snippets = _findings_text(findings)

    explanation = (
        "Correlated signals indicate multi-factor impact across domains. " + " ".join(snippets[:4])
    ).strip()
    if not snippets:
        explanation = "No strong domain signals were available to produce a confident diagnosis."

    recommendations: list[str] = []
    if any(rc.domain == "inventory" for rc in root_causes):
        recommendations.append("Restock impacted SKU and monitor stockout recurrence.")
    if any(rc.domain == "marketing" for rc in root_causes):
        recommendations.append("Re-activate or retune paused campaigns tied to the affected SKU.")
    if not recommendations and memory:
        recommendations.extend(memory.recommended_actions_from_history[:2])

    confidence = 0.6
    if root_causes:
        confidence = min(0.93, sum(rc.confidence for rc in root_causes) / len(root_causes))

    return {
        "synthesis": SynthesisResult(
            correlated_explanation=explanation,
            root_causes=root_causes,
            contributing_factors={d: "signal detected" for d in domains_correlated},
            confidence_score=confidence,
            recommendations=recommendations,
            domains_correlated=domains_correlated,
        )
    }
