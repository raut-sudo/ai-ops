from __future__ import annotations

from unittest.mock import patch

import pytest

from app.graph.nodes._react_domain import run_domain_react_agent


@pytest.mark.asyncio
async def test_exception_returns_error_finding() -> None:
    state = {"query": "test", "messages": []}

    patch_credentials = patch(
        "app.graph.nodes._react_domain.settings.AZURE_OPENAI_API_KEY", "test-key"
    )
    patch_endpoint = patch(
        "app.graph.nodes._react_domain.settings.AZURE_OPENAI_ENDPOINT",
        "https://test.openai.azure.com",
    )
    patch_wait_for = patch(
        "app.graph.nodes._react_domain.asyncio.wait_for",
        side_effect=RuntimeError("simulated agent failure"),
    )

    with patch_credentials, patch_endpoint, patch_wait_for:
        result = await run_domain_react_agent(state, "inventory")

    finding = result["domain_findings"]["inventory"]
    assert finding.confidence == 0.0
    assert "ReAct agent error" in finding.findings[0]
    assert finding.tool_calls_made == []


@pytest.mark.asyncio
async def test_no_credentials_triggers_fallback() -> None:
    state = {"query": "test", "messages": []}

    patch_key = patch("app.graph.nodes._react_domain.settings.AZURE_OPENAI_API_KEY", "")
    patch_endpoint = patch("app.graph.nodes._react_domain.settings.AZURE_OPENAI_ENDPOINT", "")
    patch_fallback = patch(
        "app.graph.nodes._react_domain._fallback",
        return_value={"domain_findings": {"inventory": None}},
    )

    with patch_key, patch_endpoint, patch_fallback as fallback:
        result = await run_domain_react_agent(state, "inventory")

    fallback.assert_awaited_once()
    assert "inventory" in result["domain_findings"]
