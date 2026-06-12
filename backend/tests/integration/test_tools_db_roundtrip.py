from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.db.session import get_session
from app.schemas import ActionProposal, RestockParams
from app.tools.actions import restock_product
from app.tools.inventory import get_stock_level
from app.tools.sales import get_sales_metrics
from app.tools.support import get_support_metrics

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

    result = await restock_product(proposal)
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


@pytest.mark.asyncio
async def test_tool_reads_return_live_db_data() -> None:
    sales = await get_sales_metrics("yesterday")
    support = await get_support_metrics("yesterday")

    sales_names = {m.name for m in sales}
    support_names = {m.name for m in support}

    assert {"revenue", "order_count", "average_order_value", "units_sold"}.issubset(sales_names)
    assert {"ticket_count", "average_sentiment", "negative_ticket_count"}.issubset(support_names)
