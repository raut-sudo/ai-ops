from __future__ import annotations

import pytest

from app.tools.marketing import (
    get_active_campaigns_for_sku,
    get_campaign_performance,
    get_underperforming_campaigns,
)

pytestmark = pytest.mark.usefixtures("ensure_seed_data")


@pytest.mark.asyncio
async def test_campaign_performance_has_derived_metrics() -> None:
    rows = await get_campaign_performance("last_7_days")

    assert len(rows) > 0
    sample = rows[0]
    assert "roas" in sample
    assert "ctr_pct" in sample


@pytest.mark.asyncio
async def test_get_active_campaigns_for_seed_sku() -> None:
    rows = await get_active_campaigns_for_sku("SKU-101")

    assert len(rows) >= 1
    assert any(row["name"] == "Summer Sale" for row in rows)


@pytest.mark.asyncio
async def test_underperforming_campaigns_threshold_filter() -> None:
    rows = await get_underperforming_campaigns("last_7_days", roas_threshold=1000.0)

    assert len(rows) > 0
    assert all(row["roas"] < 1000.0 for row in rows)
