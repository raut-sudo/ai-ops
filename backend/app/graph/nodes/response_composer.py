"""Response Composer node — LLM-driven final response assembly.

Replaces the deterministic string assembly in aggregator.py with an LLM
that composes a natural-language summary for the user. All structural fields
(status, confidence, root_causes, etc.) are still computed deterministically.

Side effect: best-effort incident persistence to Postgres + Qdrant (never raises).
"""

from __future__ import annotations

import logging
import uuid

import structlog
from langchain.agents import create_agent
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel
from sqlalchemy import text

from app.config import settings
from app.db.session import get_session
from app.embeddings import embed_text
from app.graph.prompts import load_prompt
from app.graph.state import AgentState
from app.schemas import FinalResponse
from app.vector import qdrant_upsert

log = logging.getLogger("response_composer")
logger = structlog.get_logger(__name__)

_DIAGNOSTIC_INTENTS = {
    "business_diagnosis",
    "cross_domain_analysis",
    "inventory_check",
    "marketing_analysis",
    "support_analysis",
}


class ResponseSummary(BaseModel):
    """Single-field structured output for the LLM-composed summary."""

    summary: str


# ── Incident persistence (unchanged from aggregator) ─────────────────────


async def _persist_incident(state: AgentState) -> None:
    """Best-effort write of resolved diagnosis to Postgres incidents + Qdrant.

    Gated on diagnostic intent with root causes (FR-15).
    Never raises — failure is logged only.
    """
    intent = state.get("intent")
    synth = state.get("synthesis")

    if intent is None or intent.intent_type not in _DIAGNOSTIC_INTENTS:
        return
    if not (synth and synth.root_causes):
        return

    incident_id = state.get("session_id", str(uuid.uuid4()))
    summary = synth.correlated_explanation
    causes = [rc.cause for rc in synth.root_causes]
    actions_taken = [p.action_id for p in state.get("action_requests") or []]
    results = state.get("action_results") or []
    executed = [r.action_id for r in results if r.status == "executed"]
    outcome = (
        f"{len(executed)} of {len(actions_taken)} actions executed." if actions_taken else None
    )

    try:
        async with get_session() as session:
            await session.execute(
                text("""
                    INSERT INTO incidents
                        (id, occurred_at, summary, root_causes, actions_taken,
                         outcome, status, embedded)
                    VALUES
                        (:id, NOW(), :summary, :causes, :actions,
                         :outcome, 'closed', FALSE)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": incident_id,
                    "summary": summary,
                    "causes": causes,
                    "actions": actions_taken,
                    "outcome": outcome,
                },
            )
            await session.commit()

        qdrant_point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, incident_id))
        try:
            vector = await embed_text(summary)
            await qdrant_upsert(
                point_id=qdrant_point_id,
                vector=vector,
                payload={"incident_id": incident_id, "summary": summary},
            )
            async with get_session() as session:
                await session.execute(
                    text("UPDATE incidents SET embedded = TRUE WHERE id = :id"),
                    {"id": incident_id},
                )
                await session.commit()
        except Exception as qdrant_exc:
            log.warning("response_composer Qdrant upsert failed (non-fatal): %s", qdrant_exc)

    except Exception as exc:
        log.warning("response_composer persist_incident failed (non-fatal): %s", exc)
        try:
            async with get_session() as session:
                await session.execute(
                    text("""
                        INSERT INTO audit_logs
                            (id, event_type, payload, created_at)
                        VALUES
                            (:id, 'persist_incident_failed',
                             CAST(:payload AS JSONB), NOW())
                    """),
                    {"id": str(uuid.uuid4()), "payload": f'"{exc!s}"'},
                )
                await session.commit()
        except Exception:
            pass


# ── LLM-composed summary ──────────────────────────────────────────────────


def _build_composer_context(state: AgentState) -> str:
    """Assemble the full context the composer uses to draft the summary."""
    synthesis = state.get("synthesis")
    intent = state.get("intent")
    action_results = state.get("action_results") or []
    action_requests = state.get("action_requests") or []
    memory_context = state.get("memory_context")

    parts = [f"User query: {state.get('query', '')}"]
    parts.append(f"Intent: {intent.intent_type if intent else 'unknown'}")

    if synthesis:
        parts.append(f"\nDiagnosis: {synthesis.correlated_explanation}")
        if synthesis.root_causes:
            parts.append("Root causes:")
            for rc in synthesis.root_causes:
                parts.append(f"  - [{rc.domain}] {rc.cause}")
        if synthesis.recommendations:
            parts.append("Recommendations: " + "; ".join(synthesis.recommendations))

    if action_requests:
        parts.append(f"\nPending action requests: {len(action_requests)}")
    if action_results:
        executed = [r for r in action_results if r.status == "executed"]
        parts.append(f"Executed actions: {len(executed)} of {len(action_results)}")

    if memory_context and memory_context.past_incidents:
        parts.append(
            f"\nHistorical context: {len(memory_context.past_incidents)} similar past incidents found."
        )

    return "\n".join(parts)


async def _compose_summary(state: AgentState) -> str:
    """Use the LLM to draft a natural-language summary for the user.

    Falls back to the synthesis explanation if the LLM is unavailable.
    """
    synthesis = state.get("synthesis")
    intent = state.get("intent")
    error = state.get("error")

    # Orchestrator LLM failure — no intent was classified
    if intent is None:
        detail = error or "The request could not be processed."
        return f"Sorry, I was unable to understand your request. {detail}"

    # For irrelevant / memory_recall intents, skip LLM and use deterministic text
    if intent.intent_type == "irrelevant":
        return "Query is not relevant to e-commerce operations."
    if intent.intent_type == "memory_recall":
        mc = state.get("memory_context")
        if mc and mc.past_incidents:
            return f"Found {len(mc.past_incidents)} relevant past incident(s)."
        return "No relevant past incidents found."

    # For direct_action / action_only: build plain-language summary from executed action results
    if intent.action_only or intent.intent_type == "direct_action":
        action_results = state.get("action_results") or []
        action_requests = state.get("action_requests") or []
        request_map = {r.action_id: r for r in action_requests}
        lines = []
        for r in action_results:
            req = request_map.get(r.action_id)
            label = f"{req.action_type} on {req.target}" if req else r.action_id
            if r.status == "executed":
                lines.append(f"Action completed: {label}.")
                if r.result_payload:
                    payload_str = ", ".join(f"{k}={v}" for k, v in r.result_payload.items())
                    lines.append(f"Result: {payload_str}.")
            elif r.status == "skipped":
                reason = (r.result_payload or {}).get("reason", "skipped")
                lines.append(f"Action skipped: {label} ({reason}).")
            else:
                lines.append(f"Action {r.status}: {label}.")
        if not lines:
            return f"Action request received: {state.get('query', '')}. No actions were executed."
        return " ".join(lines)

    if synthesis is None:
        return "Investigation completed with limited findings."

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
            tools=[],
            system_prompt=load_prompt("response_composer"),
            response_format=ResponseSummary,
        )

        context_msg = _build_composer_context(state)
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": context_msg}]},
            config={"recursion_limit": 5},
        )

        composed: ResponseSummary = result["structured_response"]
        if composed and composed.summary:
            logger.info("response_composer_success")
            return composed.summary

    except Exception as exc:
        logger.warning("response_composer_llm_failed", error=str(exc), exc_info=True)

    # Fallback: use synthesis explanation directly
    return synthesis.correlated_explanation if synthesis else "Investigation completed."


# ── Main node ─────────────────────────────────────────────────────────────


async def response_composer_node(state: AgentState) -> dict:
    """Compose FinalResponse with LLM-drafted summary and persist incident."""
    synthesis = state.get("synthesis")
    intent = state.get("intent")
    action_results = state.get("action_results") or []
    action_requests = state.get("action_requests") or []

    # Status: derive from synthesis.status or error/irrelevant special cases
    if state.get("error"):
        status = "error"
    elif intent and intent.intent_type == "irrelevant":
        status = "irrelevant"
    elif synthesis and synthesis.status == "insufficient":
        status = "success"  # Surface what we have rather than penalising the user
    else:
        status = "success"

    # LLM-composed summary
    summary = await _compose_summary(state)

    final_response = FinalResponse(
        session_id=state["session_id"],
        query=state["query"],
        intent_type=intent.intent_type if intent else "unknown",
        status=status,
        summary=summary,
        root_causes=synthesis.root_causes if synthesis else [],
        domain_findings=state.get("domain_findings") or {},
        memory_context=state.get("memory_context"),
        recommendations=synthesis.recommendations if synthesis else [],
        proposed_actions=action_requests,
        executed_actions=action_results,
        thread_id=state["thread_id"],
        otel_trace_id=state.get("otel_trace_id", ""),
        langsmith_run_id=state.get("langsmith_run_id"),
    )

    # Best-effort persistence (never raises)
    await _persist_incident(state)

    return {"final_response": final_response}
