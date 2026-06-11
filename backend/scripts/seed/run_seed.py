"""Seed orchestrator for Sprint 2 "Operational Data Layer & Seed".

Entry point: python scripts/seed/run_seed.py

Execution order:
  1. seed_catalog: products + inventory
  2. seed_customers: customer records
  3. seed_marketing: campaigns + daily metrics
  4. seed_orders: orders + items + movements
  5. seed_inventory_events: plant zero-crossing + historical events
  6. seed_support: tickets + returns
  7. seed_memory: incidents (L3)
  8. seed_qdrant_embeddings: embed incidents to Qdrant

Exit criteria:
  - All tables populated deterministically (same seed = same data)
  - "SKU-101 Stockout Cascade" scenario fully planted
  - Qdrant incident_embeddings collection has ≥3 points (verified)

Determinism: All randomness via Faker with SEED=42 (_faker.py).
"""

import asyncio
import logging
import sys
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

# Import seed functions
from scripts.seed.seed_catalog import seed_inventory, seed_products
from scripts.seed.seed_customers import seed_customers
from scripts.seed.seed_inventory_events import seed_inventory_events
from scripts.seed.seed_marketing import seed_campaign_metrics, seed_campaigns
from scripts.seed.seed_memory import seed_incidents, seed_qdrant_embeddings
from scripts.seed.seed_orders import seed_orders
from scripts.seed.seed_support import seed_returns, seed_support_tickets

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("run_seed")


async def main():
    """Orchestrate the complete seed process."""

    # Database connection
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        logger.info("=" * 80)
        logger.info("SPRINT 2 SEED: SKU-101 Stockout Cascade Scenario")
        logger.info("=" * 80)
        logger.info(f"Database: {settings.DATABASE_URL}")
        logger.info(f"Started: {datetime.now().isoformat()}")

        async with AsyncSessionLocal() as session:
            # 1. Seed products and inventory baselines
            logger.info("\n[1/7] Seeding products and inventory...")
            sku_name_map = await seed_products(session)
            sku_list = list(sku_name_map.keys())
            await seed_inventory(session, sku_name_map)

            # 2. Seed customers
            logger.info("\n[2/7] Seeding customers...")
            customer_refs = await seed_customers(session)

            # 3. Seed campaigns
            logger.info("\n[3/7] Seeding marketing campaigns...")
            await seed_campaigns(session)
            # Extract campaign data for metrics seeding
            # For now, get all campaigns from DB
            from sqlalchemy import select

            from app.db.models import Campaign

            campaigns_stmt = select(Campaign)
            result = await session.execute(campaigns_stmt)
            campaigns_data = [
                {
                    "id": c.id,
                    "name": c.name,
                    "status": c.status,
                    "paused_at": c.paused_at,
                    "started_at": c.started_at,
                    "ends_at": c.ends_at,
                }
                for c in result.scalars().all()
            ]
            await seed_campaign_metrics(session, campaigns_data)

            # 4. Seed orders and order items
            logger.info("\n[4/7] Seeding orders and order items...")
            order_ids = await seed_orders(session, customer_refs, sku_list)

            # 5. Seed inventory events (plant zero-crossing + historical)
            logger.info("\n[5/7] Seeding inventory events (SKU-101 OOS)...")
            await seed_inventory_events(session, sku_list)

            # 6. Seed support tickets and returns
            logger.info("\n[6/7] Seeding support tickets and returns...")
            await seed_support_tickets(session, customer_refs)
            await seed_returns(session, order_ids)

            # 7. Seed long-term memory (incidents)
            logger.info("\n[7/7] Seeding long-term memory layer...")
            incidents = await seed_incidents(session)

        # 8. Embed incidents to Qdrant (outside of DB session)
        logger.info("\n[8/8] Embedding incidents to Qdrant...")
        await seed_qdrant_embeddings(incidents)

        logger.info("\n" + "=" * 80)
        logger.info("✅ SEED COMPLETE")
        logger.info("=" * 80)
        logger.info("\nPlanted Scenario: SKU-101 Stockout Cascade")
        logger.info("  - SKU-101 out of stock (yesterday 10:00)")
        logger.info("  - 'Summer Sale' campaign paused (yesterday 09:00)")
        logger.info("  - Sales dropped -35% yesterday vs baseline")
        logger.info("  - Support tickets spiked to 8 (5 on SKU-101)")
        logger.info("  - Past incident (SKU-202) available in memory")
        logger.info("\nNext: Run the diagnosis query:")
        logger.info('  curl -X POST "http://localhost:8000/chat" \\')
        logger.info('    -H "X-User-Id: seed-test" \\')
        logger.info('    -d "{\\"query\\": \\"Why did sales drop yesterday?\\"}"')

        return 0

    except Exception as e:
        logger.error(f"❌ SEED FAILED: {e}", exc_info=True)
        return 1

    finally:
        await engine.dispose()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
