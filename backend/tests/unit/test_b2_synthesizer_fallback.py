from __future__ import annotations

from unittest.mock import patch

import pytest

from app.graph.nodes.synthesizer import _deterministic_synthesis
from app.schemas import DomainFinding, MemoryContext, SynthesisResult


def make_finding(domain: str, anomaly: str | None = None) -> DomainFinding:
    return DomainFinding(
        domain=domain,
        findings=[f"{domain} signal detected."],
        metrics=[],
        anomalies=[anomaly] if anomaly else [],
        confidence=0.75,
        tool_calls_made=[f"analyze_{domain}"],
        severity="high" if anomaly else "low",
    )


class TestDeterministicSynthesis:
    def test_empty_findings(self) -> None:
        result = _deterministic_synthesis({}, None)
        assert isinstance(result, SynthesisResult)
        assert result.confidence_score == 0.5
        assert result.root_causes == []
        assert "No strong domain signals" in result.correlated_explanation

    def test_inventory_stockout_root_cause(self) -> None:
        findings = {"inventory": make_finding("inventory", "SKU-101 is out of stock.")}
        result = _deterministic_synthesis(findings, None)
        assert len(result.root_causes) == 1
        assert result.root_causes[0].domain == "inventory"
        assert "stockout" in result.root_causes[0].cause.lower()
        assert any("Restock impacted SKU" in r for r in result.recommendations)

    def test_marketing_paused_root_cause(self) -> None:
        findings = {
            "marketing": make_finding("marketing", "Paused campaign state may suppress demand.")
        }
        result = _deterministic_synthesis(findings, None)
        assert len(result.root_causes) == 1
        assert result.root_causes[0].domain == "marketing"
        assert "paused" in result.root_causes[0].cause.lower()
        assert any("Re-activate" in r for r in result.recommendations)

    def test_sales_contraction_root_cause(self) -> None:
        findings = {"sales": make_finding("sales", "revenue declined 25% yesterday.")}
        result = _deterministic_synthesis(findings, None)
        assert len(result.root_causes) == 1
        assert result.root_causes[0].domain == "sales"

    def test_support_sentiment_root_cause(self) -> None:
        findings = {"support": make_finding("support", "sentiment deterioration detected.")}
        result = _deterministic_synthesis(findings, None)
        assert len(result.root_causes) == 1
        assert result.root_causes[0].domain == "support"
        assert "sentiment" in result.root_causes[0].cause.lower()

    def test_memory_recommendations_when_no_root_causes(self) -> None:
        memory = MemoryContext(
            past_incidents=[],
            recommended_actions_from_history=[
                "Investigate supply chain.",
                "Review campaign spend.",
            ],
            relevant_outcomes=[],
        )
        result = _deterministic_synthesis({}, memory)
        assert len(result.recommendations) == 2
        assert "Investigate" in result.recommendations[0]

    def test_lookup_path_returns_direct_answer(self) -> None:
        findings = {"sales": make_finding("sales")}
        result = _deterministic_synthesis(findings, None)
        assert result.confidence_score == 0.85
        assert "[sales]" in result.correlated_explanation
        assert result.root_causes == []

    def test_error_findings_filtered_out(self) -> None:
        findings = {
            "inventory": DomainFinding(
                domain="inventory",
                findings=["ReAct agent error; reflection will retry."],
                metrics=[],
                anomalies=[],
                confidence=0.0,
                tool_calls_made=[],
                severity="low",
            ),
        }
        result = _deterministic_synthesis(findings, None)
        assert result.confidence_score == 0.5
        assert "agent error" not in result.correlated_explanation.lower()

    def test_confidence_averaged_across_root_causes(self) -> None:
        findings = {
            "inventory": make_finding("inventory", "SKU-101 is out of stock."),
            "marketing": make_finding("marketing", "Paused campaign state may suppress demand."),
        }
        result = _deterministic_synthesis(findings, None)
        assert result.confidence_score > 0.6
        assert result.confidence_score <= 0.93


@pytest.mark.asyncio
async def test_synthesizer_falls_back_when_llm_unavailable() -> None:
    from app.graph.nodes.synthesizer import synthesizer_node

    findings = {"inventory": make_finding("inventory", "SKU-101 is out of stock.")}
    state = {
        "query": "test",
        "domain_findings": findings,
        "memory_context": None,
        "intent": None,
    }

    patch_key = patch("app.graph.nodes.synthesizer.settings.AZURE_OPENAI_API_KEY", "")
    patch_endpoint = patch("app.graph.nodes.synthesizer.settings.AZURE_OPENAI_ENDPOINT", "")

    with patch_key, patch_endpoint:
        result = await synthesizer_node(state)

    assert "synthesis" in result
    synth = result["synthesis"]
    assert isinstance(synth, SynthesisResult)
    assert len(synth.root_causes) >= 1
    assert synth.root_causes[0].domain == "inventory"
