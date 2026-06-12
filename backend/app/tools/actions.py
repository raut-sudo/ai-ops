from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from app.db.session import get_session
from app.schemas import (
    ActionProposal,
    ActionResult,
    AlertParams,
    CampaignParams,
    DiscountParams,
    RestockParams,
    TicketParams,
)
from app.tools.base import tool_retry


@tool_retry
async def restock_product(proposal: ActionProposal) -> ActionResult:
    params = proposal.parameters
    if not isinstance(params, RestockParams):
        return ActionResult(
            action_id=proposal.action_id,
            status="failed",
            error="Invalid parameters for restock_product",
        )

    try:
        async with get_session() as session:
            async with session.begin():
                update_res = await session.execute(
                    text(
                        """
                        UPDATE inventory
                        SET quantity_on_hand = quantity_on_hand + :qty,
                            last_restocked_at = NOW(),
                            updated_at = NOW()
                        WHERE sku = :sku
                        """
                    ),
                    {"qty": params.quantity, "sku": params.sku},
                )

                if update_res.rowcount == 0:
                    raise ValueError(f"Inventory row not found for SKU: {params.sku}")

                row = (
                    await session.execute(
                        text("SELECT quantity_on_hand FROM inventory WHERE sku = :sku"),
                        {"sku": params.sku},
                    )
                ).one()

                await session.execute(
                    text(
                        """
                        INSERT INTO inventory_movements (
                            id, sku, movement_type, quantity_change, quantity_after,
                            reference_type, reference_id, occurred_at
                        ) VALUES (
                            :id, :sku, 'restock', :qty, :after,
                            'restock_action', :aid, :occurred_at
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "sku": params.sku,
                        "qty": params.quantity,
                        "after": int(row.quantity_on_hand),
                        "aid": proposal.action_id,
                        "occurred_at": datetime.now(UTC),
                    },
                )

        return ActionResult(
            action_id=proposal.action_id,
            status="executed",
            result_payload={
                "sku": params.sku,
                "quantity_added": params.quantity,
                "new_quantity_on_hand": int(row.quantity_on_hand),
            },
        )
    except Exception as exc:
        return ActionResult(action_id=proposal.action_id, status="failed", error=str(exc))


@tool_retry
async def pause_campaign(proposal: ActionProposal) -> ActionResult:
    params = proposal.parameters
    if not isinstance(params, CampaignParams) or params.action_type != "pause_campaign":
        return ActionResult(
            action_id=proposal.action_id,
            status="failed",
            error="Invalid parameters for pause_campaign",
        )

    try:
        async with get_session() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        """
                        UPDATE campaigns
                        SET status = 'paused', paused_at = NOW()
                        WHERE id = :campaign_id
                        """
                    ),
                    {"campaign_id": params.campaign_id},
                )
                if result.rowcount == 0:
                    raise ValueError(f"Campaign not found: {params.campaign_id}")

        return ActionResult(
            action_id=proposal.action_id,
            status="executed",
            result_payload={"campaign_id": params.campaign_id, "status": "paused"},
        )
    except Exception as exc:
        return ActionResult(action_id=proposal.action_id, status="failed", error=str(exc))


@tool_retry
async def activate_campaign(proposal: ActionProposal) -> ActionResult:
    params = proposal.parameters
    if not isinstance(params, CampaignParams) or params.action_type != "activate_campaign":
        return ActionResult(
            action_id=proposal.action_id,
            status="failed",
            error="Invalid parameters for activate_campaign",
        )

    try:
        async with get_session() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        """
                        UPDATE campaigns
                        SET status = 'active'
                        WHERE id = :campaign_id
                        """
                    ),
                    {"campaign_id": params.campaign_id},
                )
                if result.rowcount == 0:
                    raise ValueError(f"Campaign not found: {params.campaign_id}")

        return ActionResult(
            action_id=proposal.action_id,
            status="executed",
            result_payload={"campaign_id": params.campaign_id, "status": "active"},
        )
    except Exception as exc:
        return ActionResult(action_id=proposal.action_id, status="failed", error=str(exc))


@tool_retry
async def apply_discount(proposal: ActionProposal) -> ActionResult:
    params = proposal.parameters
    if not isinstance(params, DiscountParams):
        return ActionResult(
            action_id=proposal.action_id,
            status="failed",
            error="Invalid parameters for apply_discount",
        )

    campaign_id = str(uuid.uuid4())
    campaign_name = f"Auto Discount {params.sku} {datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"

    try:
        async with get_session() as session:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        INSERT INTO campaigns (
                            id, name, channel, status, budget_total, budget_spent,
                            target_skus, discount_percent, started_at, created_at
                        ) VALUES (
                            :id, :name, 'internal', 'active', 0, 0,
                            :target_skus, :discount_percent, NOW(), NOW()
                        )
                        """
                    ),
                    {
                        "id": campaign_id,
                        "name": campaign_name,
                        "target_skus": [params.sku],
                        "discount_percent": params.percent,
                    },
                )

        return ActionResult(
            action_id=proposal.action_id,
            status="executed",
            result_payload={
                "campaign_id": campaign_id,
                "sku": params.sku,
                "discount_percent": params.percent,
            },
        )
    except Exception as exc:
        return ActionResult(action_id=proposal.action_id, status="failed", error=str(exc))


@tool_retry
async def create_support_ticket(proposal: ActionProposal) -> ActionResult:
    params = proposal.parameters
    if not isinstance(params, TicketParams):
        return ActionResult(
            action_id=proposal.action_id,
            status="failed",
            error="Invalid parameters for create_support_ticket",
        )

    ticket_number = f"TKT-AUTO-{uuid.uuid4().hex[:8].upper()}"

    try:
        async with get_session() as session:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        INSERT INTO support_tickets (
                            id, ticket_number, category, priority, status,
                            subject, description, created_at
                        ) VALUES (
                            :id, :ticket_number, 'other', :priority, 'open',
                            :subject, :description, NOW()
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "ticket_number": ticket_number,
                        "priority": params.priority,
                        "subject": params.subject,
                        "description": f"Auto-generated ticket from action {proposal.action_id}",
                    },
                )

        return ActionResult(
            action_id=proposal.action_id,
            status="executed",
            result_payload={"ticket_number": ticket_number, "status": "open"},
        )
    except Exception as exc:
        return ActionResult(action_id=proposal.action_id, status="failed", error=str(exc))


@tool_retry
async def send_alert(proposal: ActionProposal) -> ActionResult:
    params = proposal.parameters
    if not isinstance(params, AlertParams):
        return ActionResult(
            action_id=proposal.action_id,
            status="failed",
            error="Invalid parameters for send_alert",
        )

    try:
        async with get_session() as session:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        INSERT INTO audit_logs (id, event_type, action_id, user_id, payload, created_at)
                        VALUES (:id, 'alert_sent', :action_id, :user_id, CAST(:payload AS JSONB), NOW())
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "action_id": proposal.action_id,
                        "user_id": None,
                        "payload": json.dumps(
                            {
                                "channel": params.channel,
                                "message": params.message,
                                "target": proposal.target,
                            }
                        ),
                    },
                )

        return ActionResult(
            action_id=proposal.action_id,
            status="executed",
            result_payload={"channel": params.channel, "sent": True},
        )
    except Exception as exc:
        return ActionResult(action_id=proposal.action_id, status="failed", error=str(exc))


ACTION_DISPATCH = {
    "restock_product": restock_product,
    "pause_campaign": pause_campaign,
    "activate_campaign": activate_campaign,
    "apply_discount": apply_discount,
    "create_support_ticket": create_support_ticket,
    "send_alert": send_alert,
}
