from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.db.session import get_session
from app.schemas import ActionProposal, AlertParams, CampaignParams, RestockParams, TicketParams
from app.tools.actions import (
    activate_campaign,
    create_support_ticket,
    pause_campaign,
    restock_product,
    send_alert,
)

pytestmark = pytest.mark.usefixtures("ensure_seed_data")


@pytest.mark.asyncio
async def test_restock_product_writes_inventory_and_movement() -> None:
    action_id = str(uuid.uuid4())

    async with get_session() as session:
        before_row = (
            await session.execute(
                text("SELECT quantity_on_hand FROM inventory WHERE sku = :sku"),
                {"sku": "SKU-101"},
            )
        ).one()
        before_qty = int(before_row.quantity_on_hand)

    proposal = ActionProposal(
        action_id=action_id,
        target="SKU-101",
        parameters=RestockParams(sku="SKU-101", quantity=10),
        risk_level="low",
        justification="unit test",
        estimated_impact="verify atomic write",
    )

    result = await restock_product(proposal)

    async with get_session() as session:
        after_row = (
            await session.execute(
                text("SELECT quantity_on_hand FROM inventory WHERE sku = :sku"),
                {"sku": "SKU-101"},
            )
        ).one()
        after_qty = int(after_row.quantity_on_hand)

        move_row = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) AS movement_count
                    FROM inventory_movements
                    WHERE reference_id = :aid
                      AND movement_type = 'restock'
                    """
                ),
                {"aid": action_id},
            )
        ).one()

    assert result.status == "executed"
    assert after_qty == before_qty + 10
    assert int(move_row.movement_count) == 1


@pytest.mark.asyncio
async def test_pause_and_activate_campaign() -> None:
    async with get_session() as session:
        campaign = (
            await session.execute(
                text("SELECT id, status FROM campaigns WHERE name = :name LIMIT 1"),
                {"name": "Summer Sale"},
            )
        ).one()
        campaign_id = str(campaign.id)

    pause_proposal = ActionProposal(
        action_id=str(uuid.uuid4()),
        target=campaign_id,
        parameters=CampaignParams(action_type="pause_campaign", campaign_id=campaign_id),
        risk_level="low",
        justification="unit test pause",
        estimated_impact="campaign state update",
    )
    pause_result = await pause_campaign(pause_proposal)

    activate_proposal = ActionProposal(
        action_id=str(uuid.uuid4()),
        target=campaign_id,
        parameters=CampaignParams(action_type="activate_campaign", campaign_id=campaign_id),
        risk_level="low",
        justification="unit test activate",
        estimated_impact="campaign state update",
    )
    activate_result = await activate_campaign(activate_proposal)

    async with get_session() as session:
        status = (
            (
                await session.execute(
                    text("SELECT status FROM campaigns WHERE id = :id"),
                    {"id": campaign_id},
                )
            )
            .one()
            .status
        )

    assert pause_result.status == "executed"
    assert activate_result.status == "executed"
    assert status == "active"


@pytest.mark.asyncio
async def test_create_support_ticket_and_send_alert() -> None:
    ticket_action_id = str(uuid.uuid4())
    alert_action_id = str(uuid.uuid4())

    ticket_proposal = ActionProposal(
        action_id=ticket_action_id,
        target="support",
        parameters=TicketParams(subject="Phase 3 tool test ticket", priority="medium"),
        risk_level="low",
        justification="unit test support ticket",
        estimated_impact="verify support write",
    )
    ticket_result = await create_support_ticket(ticket_proposal)

    alert_proposal = ActionProposal(
        action_id=alert_action_id,
        target="ops-team",
        parameters=AlertParams(channel="slack", message="Phase 3 test alert"),
        risk_level="low",
        justification="unit test alert",
        estimated_impact="verify audit write",
    )
    alert_result = await send_alert(alert_proposal)

    async with get_session() as session:
        ticket_row = (
            await session.execute(
                text("SELECT COUNT(*) AS c FROM support_tickets WHERE ticket_number = :n"),
                {"n": ticket_result.result_payload["ticket_number"]},
            )
        ).one()
        alert_row = (
            await session.execute(
                text(
                    "SELECT COUNT(*) AS c FROM audit_logs WHERE event_type = 'alert_sent' AND action_id = :a"
                ),
                {"a": alert_action_id},
            )
        ).one()

    assert ticket_result.status == "executed"
    assert alert_result.status == "executed"
    assert int(ticket_row.c) == 1
    assert int(alert_row.c) == 1
