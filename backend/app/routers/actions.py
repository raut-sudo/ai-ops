"""GET /actions/pending — approvals queue (§19.1)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import get_session

router = APIRouter(tags=["actions"])


class PendingAction(BaseModel):
    action_id: str
    session_id: str
    action_type: str
    target: str
    parameters: dict
    risk_level: str
    justification: str | None
    created_at: datetime


@router.get("/actions/pending", response_model=list[PendingAction])
async def list_pending_actions() -> list[PendingAction]:
    """Return all incident_actions with status='proposed' (approvals queue)."""
    async with get_session() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT action_id, session_id, action_type, target,
                           parameters, risk_level, justification, created_at
                    FROM incident_actions
                    WHERE status = 'proposed'
                    ORDER BY created_at ASC
                    """
                )
            )
        ).fetchall()

    return [
        PendingAction(
            action_id=r.action_id,
            session_id=r.session_id,
            action_type=r.action_type,
            target=r.target,
            parameters=dict(r.parameters) if r.parameters else {},
            risk_level=r.risk_level,
            justification=r.justification,
            created_at=r.created_at,
        )
        for r in rows
    ]
