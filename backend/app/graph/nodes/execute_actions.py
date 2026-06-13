from __future__ import annotations

import json
import uuid

from sqlalchemy import text

from app.db.session import get_session
from app.schemas import ActionProposal, ActionResult
from app.tools.actions import ACTION_DISPATCH

ACTION_TYPE_TO_DISPATCH_KEY: dict[str, str] = {
    "restock_product": "create_purchase_order",
    "apply_discount": "create_discount_offer",
    "suspend_campaign": "suspend_campaign",
    "resume_campaign": "resume_campaign",
    "create_support_ticket": "open_customer_issue",
    "send_alert": "notify_stakeholders",
}


async def execute_actions_node(state: dict) -> dict:
    """Execute approved actions with DB-level idempotency guard.

    Uses conditional status claim in incident_actions so concurrent resume calls
    cannot execute the same action twice.
    """
    decision = state.get("hitl_decision")
    if decision is None:
        return {"action_results": []}

    proposals = state.get("proposed_actions") or []
    proposal_map: dict[str, ActionProposal] = {p.action_id: p for p in proposals}
    results: list[ActionResult] = []

    for action_id in decision.approved_action_ids:
        proposal = proposal_map.get(action_id)
        if proposal is None:
            results.append(
                ActionResult(
                    action_id=action_id,
                    status="skipped",
                    result_payload={"reason": "approved_action_not_found"},
                )
            )
            continue

        async with get_session() as session:
            claim = await session.execute(
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
            await session.commit()

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
            dispatch_key = ACTION_TYPE_TO_DISPATCH_KEY.get(proposal.action_type)
            if dispatch_key is None:
                raise ValueError(f"Unknown action_type: {proposal.action_type}")
            tool = ACTION_DISPATCH[dispatch_key]
            result = await tool(proposal)
            final_status = result.status
        except Exception as exc:
            final_status = "failed"
            result = ActionResult(action_id=action_id, status="failed", error=str(exc))

        async with get_session() as session:
            await session.execute(
                text(
                    """
                    UPDATE incident_actions
                    SET status = :status, executed_at = NOW(), updated_at = NOW()
                    WHERE action_id = :aid
                    """
                ),
                {"status": final_status, "aid": action_id},
            )
            await session.execute(
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
            await session.commit()

        results.append(result)

    for rejected_id in decision.rejected_action_ids:
        results.append(
            ActionResult(
                action_id=rejected_id,
                status="skipped",
                result_payload={"reason": "rejected_by_human"},
            )
        )

    return {"action_results": results}
