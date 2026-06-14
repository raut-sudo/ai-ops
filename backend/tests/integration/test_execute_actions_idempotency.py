from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.db.models import IncidentAction, Session
from app.db.session import get_session
from app.graph.nodes.reflection import _execute_approved_actions
from app.schemas import ActionProposal, HITLDecision, RestockParams
from app.tools.inventory import get_stock_level

pytestmark = pytest.mark.usefixtures("ensure_seed_data")


@pytest.mark.asyncio
async def test_execute_actions_is_idempotent_for_same_action() -> None:
    action_id = str(uuid.uuid4())
    thread_id = f"phase3-thread-{uuid.uuid4()}"

    proposal = ActionProposal(
        action_id=action_id,
        target="SKU-101",
        parameters=RestockParams(sku="SKU-101", quantity=5),
        risk_level="low",
        justification="idempotency test",
        estimated_impact="single execution",
    )

    before = await get_stock_level("SKU-101")

    async with get_session() as session:
        session.add(
            Session(
                thread_id=thread_id,
                user_id="phase3-test",
                query="execute action",
                intent_type="direct_action",
                status="active",
            )
        )
        session.add(
            IncidentAction(
                action_id=action_id,
                session_id=thread_id,
                action_type=proposal.action_type,
                target=proposal.target,
                parameters=proposal.parameters.model_dump(),
                risk_level=proposal.risk_level,
                status="proposed",
                justification=proposal.justification,
            )
        )
        await session.commit()

    decision = HITLDecision(
        approved_action_ids=[action_id],
        rejected_action_ids=[],
        approver="tester",
    )
    state = {
        "user_id": "phase3-test",
        "proposed_actions": [proposal],
        "hitl_decision": decision,
    }
    proposals = [proposal]

    first = await _execute_approved_actions(proposals, decision, state)
    second = await _execute_approved_actions(proposals, decision, state)

    after = await get_stock_level("SKU-101")

    async with get_session() as session:
        movement_count = (
            (
                await session.execute(
                    text(
                        """
                    SELECT COUNT(*) AS c
                    FROM inventory_movements
                    WHERE reference_id = :aid
                      AND movement_type = 'restock'
                    """
                    ),
                    {"aid": action_id},
                )
            )
            .one()
            .c
        )
        final_status = (
            (
                await session.execute(
                    text("SELECT status FROM incident_actions WHERE action_id = :aid"),
                    {"aid": action_id},
                )
            )
            .one()
            .status
        )

    assert first[0].status == "executed"
    assert second[0].status == "skipped"
    assert after["quantity_on_hand"] == before["quantity_on_hand"] + 5
    assert int(movement_count) == 1
    assert final_status == "executed"

    # Teardown: restore original inventory state so test_sprint2_seed sees qty=0
    async with get_session() as session:
        await session.execute(
            text("UPDATE inventory SET quantity_on_hand = :qty WHERE sku = :sku"),
            {"qty": before["quantity_on_hand"], "sku": "SKU-101"},
        )
        await session.execute(
            text("DELETE FROM inventory_movements WHERE reference_id = :aid"),
            {"aid": action_id},
        )
        await session.commit()
