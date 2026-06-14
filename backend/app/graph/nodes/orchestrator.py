"""Orchestrator Agent — agentic routing node.

Uses create_agent (LangChain v1) with two context-enrichment tools and a rich
system prompt to classify user intent and route to the correct domain agents.

Key differences from the old intent_classifier:
  - Truly agentic: can call `recall_similar_incidents` and `get_related_policies`
    before deciding routing, rather than doing a single-shot classification.
  - No lazy imports or module-level LLM construction.
  - Falls back to a safe broad-investigation intent on error (confidence=0.1).
"""

from __future__ import annotations

import asyncio

import structlog
from langchain.agents import create_agent
from langchain_openai import AzureChatOpenAI

from app.config import settings
from app.graph.prompts import load_prompt
from app.graph.state import AgentState
from app.schemas import IntentClassification
from app.tools.orchestrator import ORCHESTRATOR_TOOLS

logger = structlog.get_logger(__name__)


def _default_intent() -> IntentClassification:
    """Safe fallback when the orchestrator agent itself fails.

    Returns broad multi-domain investigation so no signals are missed.
    The low confidence score signals to reflection that this is uncertain.
    """
    return IntentClassification(
        intent_type="business_diagnosis",
        required_domains=["sales", "inventory", "marketing", "support"],
        memory_needed=True,
        action_only=False,
        reasoning="Orchestrator error — defaulting to broad investigation.",
    )


async def orchestrator_node(state: AgentState) -> dict:
    """Classify user intent and route to the appropriate domain agents.

    Uses create_agent with recall_similar_incidents and get_related_policies
    tools so routing decisions can be grounded in historical context and
    operational policy before committing to a classification.
    """
    query = state.get("query", "")
    prior_messages = state.get("messages", [])

    try:
        llm = AzureChatOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O_MINI,
            temperature=settings.AZURE_TEMPERATURE,
        )

        agent = create_agent(
            llm,
            tools=ORCHESTRATOR_TOOLS,
            system_prompt=load_prompt("orchestrator"),
            response_format=IntentClassification,
        )

        result = await asyncio.wait_for(
            agent.ainvoke(
                {
                    "messages": [
                        *prior_messages,
                        {"role": "user", "content": query},
                    ]
                },
                config={"recursion_limit": 10},
            ),
            timeout=30.0,
        )

        intent: IntentClassification = result["structured_response"]
        if intent is None:
            raise ValueError("structured_response was None")

        logger.info(
            "orchestrator_classified",
            intent_type=intent.intent_type,
            domains=intent.required_domains,
            action_only=intent.action_only,
        )

    except Exception as exc:
        logger.warning("orchestrator_error", error=str(exc), exc_info=True)
        intent = _default_intent()

    return {
        "intent": intent,
        "retry_count": 0,
    }
