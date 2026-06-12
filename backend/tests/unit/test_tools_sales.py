from __future__ import annotations

import pytest

from app.tools.sales import get_sales_metrics

pytestmark = pytest.mark.usefixtures("ensure_seed_data")


@pytest.mark.asyncio
async def test_get_sales_metrics_yesterday_signal() -> None:
    metrics = await get_sales_metrics("yesterday")
    metric_by_name = {m.name: m for m in metrics}

    assert "order_count" in metric_by_name
    assert "revenue" in metric_by_name
    order_count = metric_by_name["order_count"]
    revenue = metric_by_name["revenue"]
    assert order_count.value > 0, "Yesterday should have orders from seeded data"
    assert revenue.delta_pct is not None, "Delta should be calculable vs previous period"
    # Seed plants ~22 total orders on yesterday vs ~35 baseline.
    # After excluding cancelled orders: order_count drops ~29-30% (deterministic with seed=42).
    # Revenue delta is smaller (~-9%) because higher-AOV orders survived — use order_count as
    # the planted dip signal.
    assert order_count.delta_pct is not None, "Order count delta should be calculable"
    assert (
        order_count.delta_pct < -25
    ), f"Seed plants significant order-count dip; got {order_count.delta_pct}%"
