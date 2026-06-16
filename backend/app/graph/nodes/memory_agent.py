"""Memory Agent — independent agentic node.

Uses create_agent (LangChain v1) with three memory search tools and a rich
system prompt to retrieve and rank relevant past incidents. Returns a
structured MemoryContext via response_format.

Key differences from memory_retrieve.py:
  - Search capabilities are exposed as tools the LLM invokes (not inline code).
  - LLM ranks results by relevance + recency and identifies recurrence patterns.
  - Falls back to an empty MemoryContext on error (never crashes the graph).
"""

from __future__ import annotations

import asyncio

import structlog
from langchain.agents import create_agent
from langchain_openai import AzureChatOpenAI

from app.config import settings
from app.graph.prompts import load_prompt
from app.schemas import MemoryContext
from app.tools.memory import MEMORY_TOOLS

logger = structlog.get_logger(__name__)


def _empty_context() -> MemoryContext:
    return MemoryContext(
        past_incidents=[],
        recommended_actions_from_history=[],
        relevant_outcomes=[],
    )


async def memory_agent_node(state: dict) -> dict:
    """Retrieve and rank relevant past incidents using agentic search tools.

    The agent calls search_incidents_vector first, falls back to
    search_incidents_text if needed, and hydrates top results with
    fetch_incident_details before outputting a structured MemoryContext.
    """
    query = state.get("query", "")
    if not query:
        return {"memory_context": _empty_context()}

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
            tools=MEMORY_TOOLS,
            system_prompt=load_prompt("memory_agent"),
            response_format=MemoryContext,
        )

        result = await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [{"role": "user", "content": query}]},
                config={"recursion_limit": 15},
            ),
            timeout=45.0,
        )

        context: MemoryContext = result["structured_response"]
        if context is None:
            raise ValueError("structured_response was None")

        logger.info(
            "memory_agent_success",
            incident_count=len(context.past_incidents),
        )

    except Exception as exc:
        logger.warning("memory_agent_error", error=str(exc), exc_info=True)
        context = _empty_context()

    return {"memory_context": context}
