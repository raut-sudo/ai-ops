from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.graph.nodes.reflection import reflection_node
from app.graph.state import AgentState
from app.schemas import (
    DomainFinding,
    IntentClassification,
    ReflectionResult,
    RootCause,
    SynthesisResult,
)


def _mock_agent(verdict: str, domains: list[str] | None = None):
    """Return a mock agent whose ainvoke resolves to the given verdict."""
    mock = AsyncMock()
    mock.ainvoke.return_value = {
        "structured_response": ReflectionResult(
            verdict=verdict,
            critique="mocked",
            domains_to_retry=domains or [],
        )
    }
    return mock


@pytest.mark.asyncio
async def test_lookup_result_passes_without_retry() -> None:
    state: AgentState = {
        "query": "what is the highest selling product",
        "intent": IntentClassification(
            intent_type="business_diagnosis",
            required_domains=["sales"],
            memory_needed=False,
            action_only=False,
            reasoning="lookup query",
        ),
        "synthesis": SynthesisResult(
            correlated_explanation="The highest selling product is Aurora Wireless Earbuds with 1,605 units sold.",
            root_causes=[],
            contributing_factors={"sales": "lookup result"},
            status="answered",
            recommendations=[],
            domains_correlated=["sales"],
        ),
        "domain_findings": {"sales": None},
        "retry_count": 1,
        "messages": [],
    }

    with patch("app.graph.nodes.reflection.create_agent", return_value=_mock_agent("pass")):
        result = await reflection_node(state)
    ref = result["reflection_result"]
    assert ref.verdict == "pass"


@pytest.mark.asyncio
async def test_non_diagnostic_intent_passes_directly() -> None:
    state: AgentState = {
        "query": "what is the highest selling product",
        "intent": IntentClassification(
            intent_type="reporting",
            required_domains=[],
            memory_needed=False,
            action_only=False,
            reasoning="reporting query",
        ),
        "synthesis": SynthesisResult(
            correlated_explanation="Some answer.",
            root_causes=[],
            contributing_factors={},
            status="answered",
            recommendations=[],
            domains_correlated=[],
        ),
        "domain_findings": {},
        "retry_count": 0,
        "messages": [],
    }

    with patch("app.graph.nodes.reflection.create_agent", return_value=_mock_agent("pass")):
        result = await reflection_node(state)
    ref = result["reflection_result"]
    assert ref.verdict == "pass"


@pytest.mark.asyncio
async def test_low_confidence_without_root_causes_still_retries() -> None:
    state: AgentState = {
        "query": "why did sales drop",
        "intent": IntentClassification(
            intent_type="business_diagnosis",
            required_domains=["sales"],
            memory_needed=False,
            action_only=False,
            reasoning="diagnosis",
        ),
        "synthesis": SynthesisResult(
            correlated_explanation="some signal",
            root_causes=[],
            contributing_factors={},
            status="insufficient",
            recommendations=[],
            domains_correlated=["sales"],
        ),
        "domain_findings": {
            "sales": DomainFinding(
                domain="sales",
                findings=["some signal"],
                metrics=[],
                anomalies=[],
                status="partial",
                tool_calls_made=[],
                severity="low",
            ),
        },
        "retry_count": 0,
        "messages": [],
    }

    with patch(
        "app.graph.nodes.reflection.create_agent",
        return_value=_mock_agent("retry_with_domains", ["sales"]),
    ):
        result = await reflection_node(state)
    ref = result["reflection_result"]
    assert ref.verdict == "retry_with_domains"


@pytest.mark.asyncio
async def test_diagnostic_with_root_causes_passes() -> None:
    state: AgentState = {
        "query": "why did sales drop",
        "intent": IntentClassification(
            intent_type="business_diagnosis",
            required_domains=["sales", "inventory"],
            memory_needed=False,
            action_only=False,
            reasoning="diagnosis",
        ),
        "synthesis": SynthesisResult(
            correlated_explanation="Multi-factor impact detected.",
            root_causes=[RootCause(cause="Stockout", domain="inventory", evidence=[])],
            contributing_factors={"inventory": "stockout"},
            status="answered",
            recommendations=["Restock SKU"],
            domains_correlated=["inventory", "sales"],
        ),
        "domain_findings": {"inventory": None, "sales": None},
        "retry_count": 0,
        "messages": [],
    }

    with patch("app.graph.nodes.reflection.create_agent", return_value=_mock_agent("pass")):
        result = await reflection_node(state)
    ref = result["reflection_result"]
    assert ref.verdict == "pass"
