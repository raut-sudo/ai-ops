from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.nodes.synthesizer import _findings_text, synthesizer_node
from app.schemas import DomainFinding, SynthesisResult


def make_finding(domain: str, anomaly: str | None = None, status: str = "ok") -> DomainFinding:
    return DomainFinding(
        domain=domain,
        findings=[f"{domain} signal detected."],
        metrics=[],
        anomalies=[anomaly] if anomaly else [],
        status=status,
        tool_calls_made=[f"analyze_{domain}"],
        severity="high" if anomaly else "low",
    )


class TestFindingsText:
    def test_skips_error_status_findings(self) -> None:
        findings = {
            "inventory": make_finding("inventory", status="error"),
            "sales": make_finding("sales", status="ok"),
        }
        texts = _findings_text(findings)
        assert not any("[inventory]" in t for t in texts)
        assert any("[sales]" in t for t in texts)

    def test_includes_ok_and_partial_findings(self) -> None:
        findings = {
            "marketing": make_finding("marketing", status="partial"),
            "support": make_finding("support", status="ok"),
        }
        texts = _findings_text(findings)
        assert any("[marketing]" in t for t in texts)
        assert any("[support]" in t for t in texts)

    def test_includes_anomalies(self) -> None:
        findings = {"inventory": make_finding("inventory", "SKU-101 out of stock.", status="ok")}
        texts = _findings_text(findings)
        assert any("anomaly" in t and "SKU-101" in t for t in texts)


class TestSynthesizerFallback:
    @pytest.mark.asyncio
    async def test_empty_findings_returns_insufficient(self) -> None:
        result = await synthesizer_node({"domain_findings": {}, "query": "test"})
        synth = result["synthesis"]
        assert isinstance(synth, SynthesisResult)
        assert synth.status == "insufficient"
        assert synth.root_causes == []

    @pytest.mark.asyncio
    async def test_llm_failure_returns_insufficient(self) -> None:
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

        with patch("app.graph.nodes.synthesizer._get_synthesis_chain", return_value=mock_chain):
            result = await synthesizer_node(
                {
                    "domain_findings": {"sales": make_finding("sales")},
                    "query": "Why did sales drop?",
                }
            )

        synth = result["synthesis"]
        assert synth.status == "insufficient"

    @pytest.mark.asyncio
    async def test_error_status_findings_yield_insufficient(self) -> None:
        """All-error findings should produce an insufficient synthesis."""
        findings = {
            "inventory": make_finding("inventory", status="error"),
        }
        result = await synthesizer_node({"domain_findings": findings, "query": "test"})
        synth = result["synthesis"]
        assert synth.status == "insufficient"

    @pytest.mark.asyncio
    async def test_multiple_domain_findings_produce_synthesis(self) -> None:
        """Multiple ok findings with anomalies should produce a non-empty synthesis."""
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(
            return_value=SynthesisResult(
                correlated_explanation="Stockout and paused campaign are root causes.",
                root_causes=[
                    {
                        "cause": "SKU-101 stockout",
                        "domain": "inventory",
                        "evidence": ["Out of stock"],
                    },
                    {
                        "cause": "Paused campaign",
                        "domain": "marketing",
                        "evidence": ["Campaign paused"],
                    },
                ],
                status="answered",
            )
        )
        with patch("app.graph.nodes.synthesizer._get_synthesis_chain", return_value=mock_chain):
            result = await synthesizer_node(
                {
                    "domain_findings": {
                        "inventory": make_finding("inventory", "SKU-101 is out of stock."),
                        "marketing": make_finding(
                            "marketing", "Paused campaign state may suppress demand."
                        ),
                    },
                    "query": "Why did sales drop?",
                }
            )
        synth = result["synthesis"]
        assert synth.status == "answered"
        assert len(synth.root_causes) >= 1


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
