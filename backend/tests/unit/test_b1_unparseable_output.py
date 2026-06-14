"""Tests for domain agent error handling after _react_domain refactor.

Verifies that each domain agent node returns a valid low-confidence
DomainFinding when the underlying LLM call fails, rather than crashing
or returning None.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.graph.nodes.inventory_agent import inventory_agent_node
from app.graph.nodes.sales_agent import sales_agent_node


@pytest.mark.asyncio
async def test_inventory_agent_exception_returns_error_finding() -> None:
    """When create_agent.ainvoke raises, inventory_agent_node returns an
    explicit low-confidence error DomainFinding instead of propagating."""
    state = {"query": "test", "messages": []}

    mock_agent = AsyncMock()
    mock_agent.ainvoke.side_effect = RuntimeError("simulated LLM failure")

    with patch("app.graph.nodes.inventory_agent.create_agent", return_value=mock_agent):
        result = await inventory_agent_node(state)

    finding = result["domain_findings"]["inventory"]
    assert finding is not None
    assert finding.confidence == 0.1
    assert finding.severity == "low"
    assert any("Agent error" in f for f in finding.findings)
    assert finding.tool_calls_made == []


@pytest.mark.asyncio
async def test_sales_agent_exception_returns_error_finding() -> None:
    """When create_agent.ainvoke raises, sales_agent_node returns an
    explicit low-confidence error DomainFinding."""
    state = {"query": "test", "messages": []}

    mock_agent = AsyncMock()
    mock_agent.ainvoke.side_effect = RuntimeError("simulated LLM failure")

    with patch("app.graph.nodes.sales_agent.create_agent", return_value=mock_agent):
        result = await sales_agent_node(state)

    finding = result["domain_findings"]["sales"]
    assert finding is not None
    assert finding.confidence == 0.1
    assert finding.severity == "low"
    assert any("Agent error" in f for f in finding.findings)


@pytest.mark.asyncio
async def test_inventory_agent_none_structured_response_returns_error_finding() -> None:
    """When structured_response is None (LLM skipped output schema), the agent
    falls back to an error finding rather than crashing."""
    state = {"query": "test", "messages": []}

    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {"structured_response": None}

    with patch("app.graph.nodes.inventory_agent.create_agent", return_value=mock_agent):
        result = await inventory_agent_node(state)

    finding = result["domain_findings"]["inventory"]
    assert finding.confidence == 0.1
