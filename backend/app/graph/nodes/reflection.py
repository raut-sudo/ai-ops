"""Reflection node — pure quality gate (PASS / RETRY).

Agentic node using create_agent() with response_format=ReflectionResult.
No tools currently — pure structured reasoning. The graph owns retry policy;
this node owns quality judgment.
"""

from __future__ import annotations

import asyncio
import json

import structlog
from langchain.agents import create_agent
from langchain_openai import AzureChatOpenAI

from app.config import settings
from app.graph.prompts import load_prompt
from app.graph.state import AgentState
from app.schemas import ReflectionResult

logger = structlog.get_logger(__name__)


def _build_context(state: AgentState) -> str:
    """Compact context message for the evaluator — no full findings dump."""
    synthesis = state.get("synthesis")
    intent = state.get("intent")
    findings = state.get("domain_findings", {}) or {}

    findings_summary = {
        domain: {
            "status": df.status,
            "finding_count": len(df.findings),
            "anomaly_count": len(df.anomalies),
            "severity": df.severity,
        }
        for domain, df in findings.items()
        if df is not None
    }

    synthesis_text = (
        synthesis.model_dump_json(indent=2) if synthesis else '{"error": "no synthesis"}'
    )

    return (
        f"Query: {state.get('query', '')}\n\n"
        f"Intent: {intent.intent_type if intent else 'unknown'}\n\n"
        f"Synthesis:\n{synthesis_text}\n\n"
        f"Domain findings summary:\n{json.dumps(findings_summary, indent=2)}"
    )


async def reflection_node(state: AgentState) -> dict:
    """Evaluate synthesis quality and return a PASS or RETRY verdict.

    Graph-level policy:
    - Retry budget exhausted → force pass without calling the LLM.
    - LLM failure → retry_with_domains (safer than auto-pass).
    """
    retry_count = state.get("retry_count", 0)

    # Graph owns retry policy — never ask the LLM once budget is gone.
    if retry_count >= settings.MAX_RETRIES:
        result = ReflectionResult(
            verdict="pass",
            critique="Retry budget exhausted; accepting available synthesis.",
            domains_to_retry=[],
        )
        logger.info("reflection_forced_pass", retry_count=retry_count)
    else:
        try:
            llm = AzureChatOpenAI(
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_API_KEY,
                api_version=settings.AZURE_OPENAI_API_VERSION,
                azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O_MINI,
                temperature=0.0,
            )

            agent = create_agent(
                llm,
                tools=[],
                system_prompt=load_prompt("reflection_agent"),
                response_format=ReflectionResult,
            )

            agent_result = await asyncio.wait_for(
                agent.ainvoke(
                    {"messages": [{"role": "user", "content": _build_context(state)}]},
                    config={"recursion_limit": 5},
                ),
                timeout=60.0,
            )

            result: ReflectionResult = agent_result["structured_response"]
            if result is None:
                raise ValueError("structured_response was None")

            logger.info("reflection_verdict", verdict=result.verdict)

        except Exception as exc:
            # Fail towards retry, not auto-pass — avoids silently accepting bad output.
            logger.warning("reflection_agent_failed", error=str(exc), exc_info=True)
            result = ReflectionResult(
                verdict="retry_with_domains",
                critique=f"Reflection evaluator unavailable ({exc}); scheduling retry.",
                domains_to_retry=[],
            )

    updates: dict = {"reflection_result": result}
    if result.verdict == "retry_with_domains":
        updates["retry_count"] = retry_count + 1

    return updates
