"""Orchestrator node - single-shot intent classification.

Classifies the user query into a structured IntentClassification using a
direct structured-output LLM call (no tools, no agent loop). The prompt
embeds all routing rules and examples; the LLM just needs to pick the right
intent_type and domains.

On failure, sets state["error"] and returns intent=None so route_after_intent
short-circuits directly to response_composer.
"""

from __future__ import annotations

import asyncio

import structlog
from langchain_openai import AzureChatOpenAI

from app.config import settings
from app.graph.prompts import load_prompt
from app.graph.state import AgentState
from app.schemas import IntentClassification

logger = structlog.get_logger(__name__)


async def orchestrator_node(state: AgentState) -> dict:
    """Classify user intent with a single structured-output LLM call.

    No tool calls, no agent loop - just a system prompt + user query ->
    IntentClassification. Fast, predictable, cheap.

    On failure, sets state["error"] and returns intent=None so
    route_after_intent short-circuits directly to response_composer.
    """
    query = state.get("query", "")

    try:
        llm = AzureChatOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O_MINI,
            temperature=0.0,
        ).with_structured_output(IntentClassification)

        intent: IntentClassification = await asyncio.wait_for(
            llm.ainvoke(
                [
                    {"role": "system", "content": load_prompt("orchestrator")},
                    {"role": "user", "content": query},
                ]
            ),
            timeout=20.0,
        )

        if intent is None:
            raise ValueError("LLM returned None for IntentClassification")

        logger.info(
            "orchestrator_classified",
            intent_type=intent.intent_type,
            domains=intent.required_domains,
            action_only=intent.action_only,
        )
        return {
            "intent": intent,
            "retry_count": 0,
        }

    except Exception as exc:
        logger.error(
            "orchestrator_failed",
            error=str(exc),
            query=query[:120],
            exc_info=True,
        )
        return {
            "intent": None,
            "retry_count": 0,
            "error": f"Orchestrator LLM failure: {exc}",
        }
