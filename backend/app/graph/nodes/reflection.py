"""Reflection node.

Intent-aware reflection that can request targeted retries and always increments
retry_count.
"""

from __future__ import annotations

from app.graph.state import AgentState
from app.schemas import ReflectionResult


async def reflection_node(state: AgentState) -> dict:
    """Reflect on synthesis quality and select pass/retry/fail."""
    intent = state.get("intent")
    if intent and intent.intent_type in {"memory_recall", "reporting"}:
        result = ReflectionResult(
            verdict="pass",
            critique="Non-diagnostic intent; no retry path needed.",
            domains_to_retry=[],
            confidence=1.0,
        )
    else:
        synthesis = state.get("synthesis")
        findings = state.get("domain_findings", {}) or {}

        if synthesis is None:
            result = ReflectionResult(
                verdict="retry_with_domains",
                critique="No synthesis available; retry key domains.",
                domains_to_retry=(intent.required_domains if intent else ["sales"]),
                missing_information=["synthesis"],
                confidence=0.3,
            )
        elif (
            not synthesis.root_causes
            and intent
            and intent.intent_type
            in {
                "business_diagnosis",
                "cross_domain_analysis",
                "inventory_check",
                "marketing_analysis",
                "support_analysis",
            }
        ):
            to_retry = [
                domain
                for domain, finding in findings.items()
                if finding.confidence < 0.75 or not finding.anomalies
            ]
            if not to_retry:
                to_retry = intent.required_domains

            result = ReflectionResult(
                verdict="retry_with_domains",
                critique="Diagnosis lacks root causes; request targeted retry.",
                domains_to_retry=to_retry,
                missing_information=["root_causes"],
                confidence=0.58,
            )
        else:
            result = ReflectionResult(
                verdict="pass",
                critique="Synthesis quality is sufficient for downstream action or response assembly.",
                domains_to_retry=[],
                confidence=max(0.7, synthesis.confidence_score if synthesis else 0.7),
            )

    return {
        "reflection_result": result,
        "retry_count": state.get("retry_count", 0) + 1,
    }
