"""GET /incidents — list + detail endpoints (Layer 3, §19.1)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import get_session

router = APIRouter(tags=["incidents"])


class IncidentSummary(BaseModel):
    id: str
    summary: str
    status: str
    occurred_at: datetime
    root_causes: list[str]
    actions_taken: list[str]


class ActionRecord(BaseModel):
    action_id: str
    action_type: str
    target: str
    status: str
    risk_level: str
    justification: str | None
    created_at: datetime


class IncidentDetail(BaseModel):
    id: str
    summary: str
    status: str
    occurred_at: datetime
    root_causes: list[str]
    actions_taken: list[str]
    outcome: str | None
    actions: list[ActionRecord]


@router.get("/incidents", response_model=list[IncidentSummary])
async def list_incidents() -> list[IncidentSummary]:
    """List all incidents (Layer 3 records) ordered by most recent."""
    async with get_session() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id, summary, status, occurred_at, root_causes, actions_taken
                    FROM incidents
                    ORDER BY occurred_at DESC
                    LIMIT 100
                    """
                )
            )
        ).fetchall()

    return [
        IncidentSummary(
            id=str(r.id),
            summary=r.summary,
            status=r.status,
            occurred_at=r.occurred_at,
            root_causes=list(r.root_causes or []),
            actions_taken=list(r.actions_taken or []),
        )
        for r in rows
    ]


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
async def get_incident(incident_id: str) -> IncidentDetail:
    """Incident detail including associated incident_actions."""
    async with get_session() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, summary, status, occurred_at, root_causes,
                           actions_taken, outcome
                    FROM incidents
                    WHERE id = :id
                    """
                ),
                {"id": incident_id},
            )
        ).fetchone()

        if row is None:
            raise HTTPException(404, f"Incident {incident_id!r} not found.")

        action_rows = (
            await session.execute(
                text(
                    """
                    SELECT action_id, action_type, target, status, risk_level,
                           justification, created_at
                    FROM incident_actions
                    WHERE incident_id = :incident_id
                    ORDER BY created_at ASC
                    """
                ),
                {"incident_id": incident_id},
            )
        ).fetchall()

    return IncidentDetail(
        id=str(row.id),
        summary=row.summary,
        status=row.status,
        occurred_at=row.occurred_at,
        root_causes=list(row.root_causes or []),
        actions_taken=list(row.actions_taken or []),
        outcome=row.outcome,
        actions=[
            ActionRecord(
                action_id=a.action_id,
                action_type=a.action_type,
                target=a.target,
                status=a.status,
                risk_level=a.risk_level,
                justification=a.justification,
                created_at=a.created_at,
            )
            for a in action_rows
        ],
    )
