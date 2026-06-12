from __future__ import annotations

import pytest

from app.tools.support import (
    get_complaints_by_sku,
    get_refund_rate,
    get_support_metrics,
    get_ticket_trends,
)

pytestmark = pytest.mark.usefixtures("ensure_seed_data")


@pytest.mark.asyncio
async def test_support_metrics_yesterday_seed_signal() -> None:
    metrics = await get_support_metrics("yesterday")
    metric_by_name = {m.name: m for m in metrics}

    ticket_count = metric_by_name["ticket_count"].value
    negative_count = metric_by_name["negative_ticket_count"].value
    assert ticket_count >= 8, f"Seed plants 8 tickets yesterday; got {ticket_count}"
    assert negative_count >= 5, f"Seed plants 5 negative tickets yesterday; got {negative_count}"


@pytest.mark.asyncio
async def test_complaints_by_sku_negative_sentiment() -> None:
    complaints = await get_complaints_by_sku("SKU-101", "yesterday")

    assert (
        len(complaints) >= 5
    ), f"Seed plants 5+ negative complaints on SKU-101 yesterday; got {len(complaints)}"
    assert all(
        item["sentiment_score"] < 0 for item in complaints
    ), "All returned complaints must have negative sentiment"


@pytest.mark.asyncio
async def test_refund_rate_and_ticket_trends_shape() -> None:
    refund_rate = await get_refund_rate("last_7_days")
    trends = await get_ticket_trends("last_7_days")

    assert refund_rate.name == "refund_rate"
    assert float(refund_rate.value) >= 0
    assert len(trends) > 0
    assert all("category" in row and "ticket_count" in row for row in trends)
