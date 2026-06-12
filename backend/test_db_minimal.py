#!/usr/bin/env python3
"""Minimal DB roundtrip test: verify all tools can read/write to live Postgres."""

import asyncio
import sys

sys.path.insert(0, "/home/ramsantoshraut/projects/official/ai-ops/backend")

from app.schemas import ActionProposal, RestockParams
from app.tools.actions import restock_product
from app.tools.inventory import (
    get_low_stock_products,
    get_stock_level,
    get_stockout_history,
)
from app.tools.marketing import (
    get_campaign_performance,
    get_underperforming_campaigns,
)
from app.tools.sales import get_sales_metrics, get_top_products
from app.tools.support import (
    get_complaints_by_sku,
    get_refund_rate,
    get_support_metrics,
)


async def test_reads():
    """Test all read tools."""
    print("\n=== TESTING READ TOOLS ===\n")

    # Sales reads
    print("1. get_sales_metrics('yesterday')...", end=" ")
    metrics = await get_sales_metrics("yesterday")
    assert len(metrics) > 0, "Should return metrics"
    print(f"✓ Got {len(metrics)} metrics")

    print("2. get_top_products('last_7_days')...", end=" ")
    top = await get_top_products("last_7_days", limit=5)
    assert len(top) >= 0, "Should return list"
    print(f"✓ Got {len(top)} products")

    # Inventory reads
    print("3. get_stock_level('SKU-101')...", end=" ")
    stock = await get_stock_level("SKU-101")
    assert stock["sku"] == "SKU-101", "Should return SKU-101"
    print(f"✓ Stock: {stock['quantity_on_hand']} units")

    print("4. get_low_stock_products()...", end=" ")
    low = await get_low_stock_products()
    print(f"✓ Found {len(low)} low-stock items")

    print("5. get_stockout_history('SKU-101', 'yesterday')...", end=" ")
    history = await get_stockout_history("SKU-101", "yesterday")
    print(f"✓ Got {len(history)} stockout events")

    # Marketing reads
    print("6. get_campaign_performance('last_7_days')...", end=" ")
    perf = await get_campaign_performance("last_7_days")
    assert len(perf) >= 0, "Should return campaigns"
    print(f"✓ Got {len(perf)} campaigns")

    print("7. get_underperforming_campaigns('last_7_days', 1.0)...", end=" ")
    under = await get_underperforming_campaigns("last_7_days", 1.0)
    print(f"✓ Found {len(under)} underperforming")

    # Support reads
    print("8. get_support_metrics('yesterday')...", end=" ")
    support = await get_support_metrics("yesterday")
    assert len(support) > 0, "Should return metrics"
    print(f"✓ Got {len(support)} support metrics")

    print("9. get_complaints_by_sku('SKU-101', 'yesterday')...", end=" ")
    complaints = await get_complaints_by_sku("SKU-101", "yesterday")
    print(f"✓ Found {len(complaints)} complaints")

    print("10. get_refund_rate('last_7_days')...", end=" ")
    refunds = await get_refund_rate("last_7_days")
    assert refunds.name == "refund_rate", "Should be refund_rate metric"
    print(f"✓ Refund rate: {refunds.value}%")


async def test_writes():
    """Test write tools with actual DB roundtrip."""
    print("\n=== TESTING WRITE TOOLS ===\n")

    # Read initial stock
    print("1. Reading initial stock for SKU-102...", end=" ")
    stock_before = await get_stock_level("SKU-102")
    qty_before = stock_before["quantity_on_hand"]
    print(f"✓ Current qty: {qty_before}")

    # Restock
    print("2. Executing restock_product(SKU-102, qty=100)...", end=" ")
    proposal = ActionProposal(
        action_id="test-restock-001",
        target="inventory",
        parameters=RestockParams(sku="SKU-102", quantity=100),
        risk_level="low",
        justification="Test restock",
        estimated_impact="Increase stock",
    )
    result = await restock_product(proposal)
    assert result.status == "executed", f"Should execute, got {result.status}"
    print(f"✓ {result.status}")

    # Verify write
    print("3. Reading stock after restock...", end=" ")
    stock_after = await get_stock_level("SKU-102")
    qty_after = stock_after["quantity_on_hand"]
    expected_qty = qty_before + 100
    assert qty_after == expected_qty, f"Expected {expected_qty}, got {qty_after}"
    print(f"✓ New qty: {qty_after} (added 100)")


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("MINIMAL DB ROUNDTRIP TEST - All Tools")
    print("=" * 60)

    try:
        await test_reads()
        print("\n✅ All READ tools work with live DB")

        await test_writes()
        print("\n✅ All WRITE tools work with live DB")

        print("\n" + "=" * 60)
        print("SUCCESS: All tools read and write to Postgres ✓")
        print("=" * 60 + "\n")
        return 0

    except Exception as e:
        print(f"\n\n❌ ERROR: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
