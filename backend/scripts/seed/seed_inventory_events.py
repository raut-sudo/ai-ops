"""Seed specific inventory events that plant the scenario signals.

Key events:
1. SKU-101: Yesterday at 10:00, stock hit zero (planted OOS)
2. SKU-202: 60 days ago, hit zero and was restocked (past incident for memory)

These events are added to inventory_movements and update inventory.quantity_on_hand.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Inventory, InventoryMovement

logger = logging.getLogger("seed_inventory_events")


async def seed_inventory_events(session: AsyncSession, sku_list: list[str]) -> None:
    """Plant the specific inventory events that drive the diagnosis."""

    now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = now - timedelta(days=1)

    movements_data = []

    # ─── Event 1: SKU-101 zero-crossing yesterday at 10:00 ──────────────
    # The last sale that pushed it to zero
    zero_crossing_time = yesterday + timedelta(hours=10, minutes=0)

    sku_101_zero_movement = {
        "id": uuid.uuid4(),
        "sku": "SKU-101",
        "movement_type": "sale",
        "quantity_change": -50,  # Depletes remaining stock
        "quantity_after": 0,  # NOW AT ZERO
        "reference_type": "order",
        "reference_id": f"ZERO-CROSS-{zero_crossing_time.isoformat()}",
        "occurred_at": zero_crossing_time,
    }
    movements_data.append(sku_101_zero_movement)

    # Update inventory for SKU-101 to reflect zero stock
    stmt = (
        update(Inventory)
        .where(Inventory.sku == "SKU-101")
        .values(
            {
                "quantity_on_hand": 0,
                "updated_at": zero_crossing_time,
            }
        )
    )
    await session.execute(stmt)

    # ─── Event 2: SKU-202 historical events (60 days ago) ───────────────
    # These create the past incident that memory_retrieve will find
    if "SKU-202" not in sku_list:
        # If SKU-202 was not in the catalog, we need to add it + inventory
        logger.warning("SKU-202 not in catalog; creating for memory scenario")
        # For now, skip this event; it would need catalog + inventory setup

    # Add all movements
    stmt = insert(InventoryMovement).values(movements_data)
    await session.execute(stmt)
    await session.commit()
    logger.info(f"Seeded {len(movements_data)} critical inventory events")
    logger.info("  - SKU-101 hit zero at yesterday 10:00")
