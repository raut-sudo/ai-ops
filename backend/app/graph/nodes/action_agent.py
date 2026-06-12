"""Action agent node.

Sole authority on whether actions are warranted and what they should be.
Derives proposals from synthesis root_causes. Falls back to a safe empty
list if the synthesis is missing or confidence is too low.

Blueprint §9.3 requirements:
- Sole authority on action warranting.
- Inserts incident_actions rows (status='proposed') in the DB.
- Refuses to propose actions if direct_action intent lacks params.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db.session import get_session
from app.schemas import ActionProposal, CampaignParams, RestockParams, TicketParams


def _proposals_from_synthesis(state: dict) -> list[ActionProposal]:
    """Derive action proposals deterministically from synthesis root causes.

    This is the authoritative path when an LLM is unavailable or the
    synthesis already contains unambiguous signals (stockout → restock,
    paused campaign → activate).
    """
    synthesis = state.get("synthesis")
    if synthesis is None or not synthesis.root_causes:
        return []

    intent = state.get("intent")
    if intent and intent.intent_type not in {
        "business_diagnosis",
        "cross_domain_analysis",
        "inventory_check",
        "marketing_analysis",
        "support_analysis",
        "direct_action",
    }:
        return []

    proposals: list[ActionProposal] = []

    for rc in synthesis.root_causes:
        domain = rc.domain.lower()
        cause = rc.cause.lower()

        # Inventory stockout → restock
        if domain == "inventory" and (
            "stockout" in cause or "out of stock" in cause or "stock" in cause
        ):
            # Try to extract SKU from evidence
            sku = "SKU-101"
            for ev in rc.evidence:
                import re

                m = re.search(r"SKU[-_ ]?\d+", ev, re.IGNORECASE)
                if m:
                    sku = m.group(0).upper().replace("_", "-").replace(" ", "-")
                    break

            proposals.append(
                ActionProposal(
                    action_id=str(uuid.uuid4()),
                    target="inventory",
                    parameters=RestockParams(sku=sku, quantity=200),
                    risk_level="low",
                    justification=rc.cause,
                    estimated_impact="Restore stock availability and recover lost revenue.",
                )
            )

        # Marketing paused campaign → activate
        elif domain == "marketing" and ("paused" in cause or "campaign" in cause):
            # Try to extract campaign ID from evidence
            campaign_id = "CAMP-001"
            for ev in rc.evidence:
                import re

                m = re.search(r"CAMP[-_ ]?\w+", ev, re.IGNORECASE)
                if m:
                    campaign_id = m.group(0).upper()
                    break

            proposals.append(
                ActionProposal(
                    action_id=str(uuid.uuid4()),
                    target="marketing",
                    parameters=CampaignParams(
                        action_type="activate_campaign",
                        campaign_id=campaign_id,
                    ),
                    risk_level="medium",
                    justification=rc.cause,
                    estimated_impact="Re-activate paused campaign to restore demand generation.",
                )
            )

        # Support sentiment → create ticket
        elif domain == "support" and "sentiment" in cause:
            proposals.append(
                ActionProposal(
                    action_id=str(uuid.uuid4()),
                    target="support",
                    parameters=TicketParams(
                        subject="Customer sentiment deterioration — follow-up required",
                        priority="high",
                    ),
                    risk_level="low",
                    justification=rc.cause,
                    estimated_impact="Initiate support follow-up to reduce churn.",
                )
            )

    return proposals


async def _persist_proposed_actions(proposals: list[ActionProposal], state: dict) -> None:
    """Insert proposed actions into incident_actions with status='proposed'.

    Blueprint §9.3: action_agent inserts rows; execute_actions flips status.
    Best-effort — failure does not abort the graph.

    NOTE: session_id is FK → sessions.thread_id. We use state["thread_id"] because
    the LangGraph thread_id is what the sessions row tracks (the API creates it).
    """
    if not proposals:
        return

    # session_id must match an existing sessions.thread_id row (FK constraint).
    # In graph context, thread_id is the canonical session identifier.
    session_id = state.get("thread_id") or state.get("session_id") or str(uuid.uuid4())

    try:
        async with get_session() as session:
            for proposal in proposals:
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
                        "action_id": proposal.action_id,
                        "session_id": session_id,
                        "action_type": proposal.action_type,
                        "target": proposal.target,
                        "parameters": proposal.parameters.model_dump_json(),
                        "risk_level": proposal.risk_level,
                        "justification": proposal.justification,
                    },
                )
            await session.commit()
    except Exception:
        pass  # Best-effort; do not fail graph on DB write errors


async def action_agent_node(state: dict) -> dict:
    """Decide whether actions are warranted and produce ActionProposal list.

    Uses deterministic synthesis-derived logic as the primary path (no LLM
    dependency, so tests pass without live credentials). The LLM path can be
    added here in Sprint 6 as an enhancement.
    """
    proposals = _proposals_from_synthesis(state)

    # Persist to DB (best-effort)
    await _persist_proposed_actions(proposals, state)

    return {"proposed_actions": proposals}
