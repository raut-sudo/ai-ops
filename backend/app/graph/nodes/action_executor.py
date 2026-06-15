"""Action Executor node.

Handles the HITL gate: pauses via interrupt(), waits for human approval,
then executes approved actions with DB-level idempotency.

Reads action_requests from state (populated by domain agents via REQUEST_TOOLS).
"""

from __future__ import annotations

import json
import uuid

import structlog
from langgraph.types import interrupt
from sqlalchemy import text

from app.db.session import get_session
from app.graph.state import AgentState
from app.schemas import ActionRequest, ActionResult, HITLDecision
from app.tools.actions import ACTION_DISPATCH

logger = structlog.get_logger(__name__)

ACTION_TYPE_TO_DISPATCH_KEY: dict[str, str] = {
    "restock_product": "create_purchase_order",
    "apply_discount": "create_discount_offer",
    "suspend_campaign": "suspend_campaign",
    "resume_campaign": "resume_campaign",
    "create_support_ticket": "open_customer_issue",
    "send_alert": "notify_stakeholders",
}


async def _persist_action_requests(
    requests: list[ActionRequest],
    state: dict,
) -> bool:
    """Insert action requests into incident_actions with status='proposed'.

    Returns True if persistence succeeded, False otherwise.
    """
    if not requests:
        return True

    session_id = state.get("thread_id") or state.get("session_id") or str(uuid.uuid4())

    try:
        async with get_session() as session:
            for req in requests:
                await session.execute(
                    text(
                        """
                        INSERT INTO incident_actions
                            (id, action_id, session_id, action_type, target,
                             parameters, risk_level, justification, status,
                             created_at, updated_at)
                        VALUES
                            (:id, :action_id, :session_id, :action_type, :target,
                             CAST(:parameters AS JSONB), :risk_level, :justification,
                             'proposed', NOW(), NOW())
                        ON CONFLICT (action_id) DO NOTHING
                        """
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "action_id": req.action_id,
                        "session_id": session_id,
                        "action_type": req.action_type,
                        "target": req.target,
                        "parameters": req.parameters.model_dump_json(),
                        "risk_level": req.risk_level,
                        "justification": req.justification,
                    },
                )
            await session.commit()
        return True
    except Exception as exc:
        logger.error(
            "persist_action_requests_failed",
            error=str(exc),
            request_count=len(requests),
            session_id=session_id,
            exc_info=True,
        )
        return False


async def _execute_approved_actions(
    requests: list[ActionRequest],
    decision: HITLDecision,
    state: dict,
) -> list[ActionResult]:
    """Claim → dispatch → audit for each approved action. Returns list[ActionResult].

    Rejected actions are included with status='skipped'.
    DB-level idempotency: claims the row with UPDATE WHERE status='proposed'.
    """
    request_map: dict[str, ActionRequest] = {r.action_id: r for r in requests}
    results: list[ActionResult] = []

    for action_id in decision.approved_action_ids:
        req = request_map.get(action_id)
        if req is None:
            results.append(
                ActionResult(
                    action_id=action_id,
                    status="skipped",
                    result_payload={"reason": "approved_action_not_found"},
                )
            )
            continue

        # DB-level idempotency: claim the action row
        async with get_session() as db_session:
            claim = await db_session.execute(
                text(
                    """
                    UPDATE incident_actions
                    SET status = 'executing', updated_at = NOW()
                    WHERE action_id = :aid AND status = 'proposed'
                    RETURNING id
                    """
                ),
                {"aid": action_id},
            )
            claimed = claim.first() is not None
            await db_session.commit()

        if not claimed:
            results.append(
                ActionResult(
                    action_id=action_id,
                    status="skipped",
                    result_payload={"reason": "already_processed"},
                )
            )
            continue

        try:
            dispatch_key = ACTION_TYPE_TO_DISPATCH_KEY.get(req.action_type)
            if dispatch_key is None:
                raise ValueError(f"Unknown action_type: {req.action_type}")
            tool = ACTION_DISPATCH[dispatch_key]
            result = await tool(req)
            final_status = result.status
        except Exception as exc:
            final_status = "failed"
            result = ActionResult(action_id=action_id, status="failed", error=str(exc))

        # Update DB status + audit log
        async with get_session() as db_session:
            await db_session.execute(
                text(
                    """
                    UPDATE incident_actions
                    SET status = :status, executed_at = NOW(), updated_at = NOW()
                    WHERE action_id = :aid
                    """
                ),
                {"status": final_status, "aid": action_id},
            )
            await db_session.execute(
                text(
                    """
                    INSERT INTO audit_logs (id, event_type, action_id, user_id, payload)
                    VALUES (:id, 'action_executed', :aid, :uid, CAST(:payload AS JSONB))
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "aid": action_id,
                    "uid": state.get("user_id"),
                    "payload": json.dumps(result.model_dump(mode="json")),
                },
            )
            await db_session.commit()

        results.append(result)

    # Record rejected actions
    for rejected_id in decision.rejected_action_ids:
        results.append(
            ActionResult(
                action_id=rejected_id,
                status="skipped",
                result_payload={"reason": "rejected_by_human"},
            )
        )

    return results


async def action_executor_node(state: AgentState) -> dict:
    """Execute approved actions after HITL approval.

    This node:
    1. Persists action_requests to DB (idempotent via ON CONFLICT)
    2. Calls interrupt() to pause for human approval (HITL gate)
    3. On resume, receives the HITLDecision
    4. Executes approved actions with DB-level idempotency

    Because interrupt() is called HERE (not in reflection_node), when the graph
    resumes it re-enters THIS node. The requests in state are stable (generated
    by domain agents in earlier steps), so the action_ids always match.
    """
    requests = state.get("action_requests") or []

    if not requests:
        # No requests — nothing to do
        return {}

    # Persist before interrupt so DB rows exist when the approval arrives
    await _persist_action_requests(requests, state)

    # ── HITL Gate: pause for human approval ──
    synthesis = state.get("synthesis")
    summary = synthesis.correlated_explanation if synthesis else ""

    requests_payload = [r.model_dump(mode="json") for r in requests]

    decision_raw = interrupt(
        {
            "proposed_actions": requests_payload,
            "session_id": state.get("session_id"),
            "summary": summary,
        }
    )

    # ── After resume: parse decision and execute ──
    decision = HITLDecision.model_validate(decision_raw)
    results = await _execute_approved_actions(requests, decision, state)
    return {"hitl_decision": decision, "action_results": results}
