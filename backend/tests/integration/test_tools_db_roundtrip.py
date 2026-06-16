from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.db.session import get_session
from app.schemas import ActionProposal, RestockParams
from app.tools.actions import create_purchase_order
from app.tools.inventory import get_stock_level
from app.tools.sales import analyze_sales
from app.tools.support import analyze_support

pytestmark = pytest.mark.usefixtures("ensure_seed_data")


@pytest.mark.asyncio
async def test_tool_read_write_roundtrip_inventory() -> None:
    action_id = str(uuid.uuid4())

    before = await get_stock_level("SKU-101")

    proposal = ActionProposal(
        action_id=action_id,
        target="SKU-101",
        parameters=RestockParams(sku="SKU-101", quantity=7),
        risk_level="low",
        justification="integration roundtrip",
        estimated_impact="prove write path",
    )

    result = await create_purchase_order(proposal)
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

    assert result.status == "executed"
    assert after["quantity_on_hand"] == before["quantity_on_hand"] + 7
    assert int(movement_count) == 1

    # Teardown: restore original quantity so seed-scenario tests are not polluted
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


@pytest.mark.asyncio
async def test_tool_reads_return_live_db_data() -> None:
    sales = await analyze_sales("yesterday")
    support = await analyze_support("yesterday")

    assert all(k in sales for k in ("revenue", "order_count", "average_order_value", "units_sold"))
    assert all(k in support for k in ("ticket_count", "average_sentiment", "negative_ticket_count"))
