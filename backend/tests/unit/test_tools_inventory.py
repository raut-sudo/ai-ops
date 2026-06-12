from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.tools.inventory import (
    get_low_stock_products,
    get_stock_level,
    get_stockout_history,
    was_out_of_stock,
)

pytestmark = pytest.mark.usefixtures("ensure_seed_data")


@pytest.mark.asyncio
async def test_get_stock_level_for_seed_sku() -> None:
    stock = await get_stock_level("SKU-101")

    assert stock["sku"] == "SKU-101"
    assert isinstance(stock["quantity_on_hand"], int)
    assert "reorder_point" in stock


@pytest.mark.asyncio
async def test_low_stock_and_stockout_history() -> None:
    low_stock = await get_low_stock_products()
    low_skus = {item["sku"] for item in low_stock}

    assert "SKU-101" in low_skus

    history = await get_stockout_history("SKU-101", "last_7_days")
    assert len(history) >= 1
    assert any(entry["quantity_after"] <= 0 for entry in history)


@pytest.mark.asyncio
async def test_was_out_of_stock_yesterday_window() -> None:
    ts = datetime.now(UTC).replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=1)
    result = await was_out_of_stock("SKU-101", ts)

    assert result is True
