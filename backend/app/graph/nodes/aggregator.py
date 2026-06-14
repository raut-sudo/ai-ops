"""Aggregator node — terminal node.

Combines:
- Final response assembly (was assemble_response.py)
- Incident persistence to Postgres + Qdrant (was persist_incident.py)

This is the only exit point to END. It never raises — failures are logged
but must never block the user-facing response (NFR-12).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text

from app.db.session import get_session
from app.embeddings import embed_text
from app.graph.state import AgentState
from app.schemas import FinalResponse
from app.vector import qdrant_upsert

log = logging.getLogger("aggregator")

_DIAGNOSTIC_INTENTS = {
    "business_diagnosis",
    "cross_domain_analysis",
    "inventory_check",
    "marketing_analysis",
    "support_analysis",
}


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
    actions_taken = [p.action_id for p in state.get("proposed_actions") or []]
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
            log.warning("aggregator Qdrant upsert failed (non-fatal): %s", qdrant_exc)

    except Exception as exc:
        log.warning("aggregator persist_incident failed (non-fatal): %s", exc)
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


async def aggregator_node(state: AgentState) -> dict:
    """Build FinalResponse and best-effort persist incident. Terminal node."""
    synthesis = state.get("synthesis")
    intent = state.get("intent")
    action_results = state.get("action_results") or []
    proposed_actions = state.get("proposed_actions") or []

    # Confidence: prefer synthesis score, fall back to reflection
    confidence: float = 0.0
    if synthesis:
        confidence = synthesis.confidence_score
    elif state.get("reflection_result"):
        confidence = state["reflection_result"].confidence  # type: ignore[union-attr]

    # Status
    if state.get("error"):
        status = "error"
    elif intent and intent.intent_type == "irrelevant":
        status = "irrelevant"
    elif confidence < 0.5:
        status = "low_confidence"
    else:
        status = "success"

    # Summary
    if synthesis:
        summary = synthesis.correlated_explanation
    elif intent and intent.intent_type == "irrelevant":
        summary = "Query is not relevant to e-commerce operations."
    elif intent and intent.intent_type == "memory_recall":
        mc = state.get("memory_context")
        if mc and mc.past_incidents:
            summary = f"Found {len(mc.past_incidents)} relevant past incident(s)."
        else:
            summary = "No relevant past incidents found."
    else:
        summary = "Investigation completed with limited findings."

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
        proposed_actions=proposed_actions,
        executed_actions=action_results,
        confidence_score=confidence,
        low_confidence_flag=confidence < 0.5,
        thread_id=state["thread_id"],
        otel_trace_id=state.get("otel_trace_id", ""),
        langsmith_run_id=state.get("langsmith_run_id"),
    )

    # Best-effort persistence (never raises)
    await _persist_incident(state)

    return {"final_response": final_response}
