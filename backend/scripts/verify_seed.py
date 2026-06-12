"""Verification script for Sprint 2 seed execution.

Runs:
  1. Clean database (drop all tables)
  2. Recreate schema (create all tables)
  3. Execute seed (populate with scenario data)
  4. Verify key data points (cross-domain integrity)
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db.models import Base, Campaign, Incident, Inventory, Order, Product, SupportTicket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("verify_seed")


async def reset_database():
    """Drop and recreate all tables."""
    logger.info("🔄 Resetting database...")
    engine = create_async_engine(settings.DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    await engine.dispose()
    logger.info("✅ Database reset complete")


async def run_seed_execution():
    """Execute the seed scenario."""
    logger.info("🌱 Running seed...")
    try:
        # The run_seed function expects no arguments and uses settings.DATABASE_URL
        from scripts.seed.run_seed import main

        await main()
        logger.info("✅ Seed execution complete")
    except Exception as e:
        logger.error(f"❌ Seed execution failed: {e}", exc_info=True)
        raise


async def verify_key_data_points():
    """Verify critical scenario data points."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    checks_passed = 0
    checks_failed = 0

    async with AsyncSessionLocal() as session:
        # Check 1: Product count
        stmt = select(func.count(Product.sku))
        result = await session.execute(stmt)
        product_count = result.scalar_one()
        if product_count == 30:
            logger.info(f"✅ Products: {product_count}")
            checks_passed += 1
        else:
            logger.error(f"❌ Products: Expected 30, got {product_count}")
            checks_failed += 1

        # Check 2: Order count (should be ~3100-3200 due to randomized orders/day)
        stmt = select(func.count(Order.id))
        result = await session.execute(stmt)
        order_count = result.scalar_one()
        if 3000 <= order_count <= 3200:
            logger.info(f"✅ Orders: {order_count} (expected ~3000-3200)")
            checks_passed += 1
        else:
            logger.error(f"❌ Orders: Expected ~3000-3200, got {order_count}")
            checks_failed += 1

        # Check 3: Yesterday orders dip to 22
        from datetime import timedelta

        now = datetime.now(UTC)
        yesterday = (now - timedelta(days=1)).date()

        stmt = select(func.count(Order.id)).where(func.date(Order.placed_at) == yesterday)
        result = await session.execute(stmt)
        yesterday_orders = result.scalar_one()
        if yesterday_orders == 22:
            logger.info(f"✅ Yesterday orders dip: {yesterday_orders} (expected 22)")
            checks_passed += 1
        else:
            logger.error(f"❌ Yesterday orders dip: Expected 22, got {yesterday_orders}")
            checks_failed += 1

        # Check 4: SKU-101 inventory zero
        stmt = select(Inventory.quantity_on_hand).where(Inventory.sku == "SKU-101")
        result = await session.execute(stmt)
        sku101_qty = result.scalar_one_or_none()
        if sku101_qty == 0:
            logger.info(f"✅ SKU-101 inventory: {sku101_qty} (OOS)")
            checks_passed += 1
        else:
            logger.error(f"❌ SKU-101 inventory: Expected 0, got {sku101_qty}")
            checks_failed += 1

        # Check 5: Summer Sale paused
        stmt = select(Campaign).where(Campaign.name == "Summer Sale")
        result = await session.execute(stmt)
        campaign = result.scalar_one_or_none()
        if campaign and campaign.status == "paused":
            logger.info(f"✅ Summer Sale: {campaign.status}")
            checks_passed += 1
        else:
            logger.error(
                f"❌ Summer Sale: Expected status=paused, got {campaign.status if campaign else 'NOT FOUND'}"
            )
            checks_failed += 1

        # Check 6: Support spike yesterday (8 tickets)
        stmt = select(func.count(SupportTicket.id)).where(
            func.date(SupportTicket.created_at) == yesterday
        )
        result = await session.execute(stmt)
        yesterday_tickets = result.scalar_one()
        if yesterday_tickets == 8:
            logger.info(f"✅ Support spike yesterday: {yesterday_tickets} (expected 8)")
            checks_passed += 1
        else:
            logger.error(f"❌ Support spike yesterday: Expected 8, got {yesterday_tickets}")
            checks_failed += 1

        # Check 7: ≥5 SKU-101 tickets yesterday (seed guarantees first 5, may get more)
        stmt = select(func.count(SupportTicket.id)).where(
            and_(
                func.date(SupportTicket.created_at) == yesterday,
                SupportTicket.related_sku == "SKU-101",
            )
        )
        result = await session.execute(stmt)
        sku101_tickets = result.scalar_one()
        if sku101_tickets >= 5:
            logger.info(f"✅ SKU-101 tickets yesterday: {sku101_tickets} (expected ≥5)")
            checks_passed += 1
        else:
            logger.error(f"❌ SKU-101 tickets yesterday: Expected ≥5, got {sku101_tickets}")
            checks_failed += 1

        # Check 8: Incidents exist (≥3)
        stmt = select(func.count(Incident.id))
        result = await session.execute(stmt)
        incident_count = result.scalar_one()
        if incident_count >= 3:
            logger.info(f"✅ Incidents: {incident_count} (expected ≥3)")
            checks_passed += 1
        else:
            logger.error(f"❌ Incidents: Expected ≥3, got {incident_count}")
            checks_failed += 1

    await engine.dispose()

    logger.info("\n" + "=" * 80)
    logger.info("VERIFICATION SUMMARY")
    logger.info("=" * 80)
    logger.info(f"✅ Passed: {checks_passed}/8")
    logger.info(f"❌ Failed: {checks_failed}/8")

    if checks_failed == 0:
        logger.info("\n🎉 All checks passed! Seed scenario verified successfully.")
        return 0
    else:
        logger.error(f"\n⚠️ {checks_failed} checks failed. Review logs above.")
        return 1


async def main():
    """Execute verification pipeline."""
    logger.info("=" * 80)
    logger.info("SPRINT 2 SEED VERIFICATION")
    logger.info("=" * 80)

    try:
        await reset_database()
        await run_seed_execution()
        exit_code = await verify_key_data_points()
        return exit_code
    except Exception as e:
        logger.error(f"Verification failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
