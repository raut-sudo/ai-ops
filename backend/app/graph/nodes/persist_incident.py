"""Persist incident node — gated, best-effort write to Layer 3.

Blueprint §15.3, §30.6 requirements:
- Gate: only persists for diagnostic intents with root causes (FR-15).
- Best-effort: Qdrant or Postgres failure MUST NOT fail the user response (NFR-12).
- Separate from assemble_response so assemble is always pure (no I/O).
- Failures logged to audit_logs (never raise).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text

from app.db.session import get_session
from app.embeddings import embed_text
from app.vector import qdrant_upsert

log = logging.getLogger("persist_incident")

_DIAGNOSTIC_INTENTS = {
    "business_diagnosis",
    "cross_domain_analysis",
    "inventory_check",
    "marketing_analysis",
    "support_analysis",
}


async def persist_incident_node(state: dict) -> dict:
    """Best-effort write of resolved diagnosis to Postgres incidents + Qdrant.

    Returns {} always — failure must never propagate (NFR-12).
    """
    intent = state.get("intent")
    synth = state.get("synthesis")

    # ── GATE (FR-15): only real diagnoses with root causes ──
    if intent is None or intent.intent_type not in _DIAGNOSTIC_INTENTS:
        return {}
    if not (synth and synth.root_causes):
        return {}

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
        # ── Postgres write ──
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

        # ── Qdrant write (separate try so Postgres write is not lost) ──
        # Use deterministic UUID5 from incident_id so Qdrant gets a valid UUID.
        qdrant_point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, incident_id))
        try:
            vector = await embed_text(summary)
            await qdrant_upsert(
                point_id=qdrant_point_id,
                vector=vector,
                payload={"incident_id": incident_id, "summary": summary},
            )
            # Mark embedded = True in Postgres
            async with get_session() as session:
                await session.execute(
                    text("UPDATE incidents SET embedded = TRUE WHERE id = :id"),
                    {"id": incident_id},
                )
                await session.commit()
        except Exception as qdrant_exc:
            log.warning("persist_incident Qdrant upsert failed (non-fatal): %s", qdrant_exc)

    except Exception as exc:
        # NFR-12: never fail the user-facing response
        log.warning("persist_incident failed (non-fatal): %s", exc)
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

    return {}
