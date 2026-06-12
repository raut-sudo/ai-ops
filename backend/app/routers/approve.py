"""POST /approve — idempotent, stall-proof HITL resume (§17.3).

Two cases (blueprint §17.3):
  Case A: thread already completed → return existing final_response (FR-17).
  Case B: thread genuinely paused  → resume with Command(resume=decision).

The ONLY pause predicate is _is_awaiting_hitl(snapshot) (§30.2).
No app flags; no separate state booleans.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Header, HTTPException
from langgraph.types import Command

from app.graph.hitl_utils import _is_awaiting_hitl
from app.graph.runtime import get_compiled_graph
from app.schemas import ApproveRequest, FinalResponse

log = structlog.get_logger(__name__)

router = APIRouter(tags=["approve"])


@router.post("/approve", response_model=FinalResponse)
async def approve(
    body: ApproveRequest,
    x_user_id: str = Header(..., alias="X-User-Id"),
) -> FinalResponse:
    """Submit a HITL decision; idempotent resume via checkpoint (§17.3).

    * Case A (FR-17): thread already completed → return existing final_response.
    * Case B: thread paused → resume with Command(resume=decision).
    """
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": body.thread_id}}

    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        log.exception("approve.aget_state_error", thread_id=body.thread_id, error=str(exc))
        raise HTTPException(500, f"Failed to read graph state: {exc}") from exc

    # ── Case A: already completed (idempotent re-approve, FR-17) ────────────
    if not _is_awaiting_hitl(snapshot):
        fr = snapshot.values.get("final_response") if snapshot.values else None
        if fr is not None:
            log.info("approve.case_a.already_completed", thread_id=body.thread_id)
            return FinalResponse.model_validate(fr) if not isinstance(fr, FinalResponse) else fr
        raise HTTPException(
            409,
            "Thread is neither awaiting HITL nor has a completed final_response.",
        )

    # ── Case B: genuinely paused → resume once (§17.3) ──────────────────────
    # Populate approver from header if body.decision.approver is empty.
    decision = body.decision
    if not decision.approver:
        from app.schemas import HITLDecision

        decision = HITLDecision(
            approved_action_ids=decision.approved_action_ids,
            rejected_action_ids=decision.rejected_action_ids,
            approver=x_user_id,
            rejection_reason=decision.rejection_reason,
        )

    log.info(
        "approve.case_b.resuming",
        thread_id=body.thread_id,
        approved=decision.approved_action_ids,
        rejected=decision.rejected_action_ids,
    )

    try:
        result = await graph.ainvoke(Command(resume=decision.model_dump()), config)
    except Exception as exc:
        log.exception("approve.resume_error", thread_id=body.thread_id, error=str(exc))
        raise HTTPException(500, f"Graph resume failed: {exc}") from exc

    fr = result.get("final_response")
    if fr is None:
        raise HTTPException(500, "Graph completed but produced no final_response.")

    return FinalResponse.model_validate(fr) if not isinstance(fr, FinalResponse) else fr
