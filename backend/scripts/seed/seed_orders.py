"""Seed orders and order items over 90 days.

Key constraint: yesterday's orders are significantly lower (~22 orders vs ~35 baseline).
This is the primary symptom the diagnosis system must detect.

Also creates inventory_movements entries for each sale (reference_type='order').
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InventoryMovement, Order, OrderItem

from ._faker import fake

logger = logging.getLogger("seed_orders")


async def seed_orders(
    session: AsyncSession,
    customer_refs: list[tuple],
    sku_list: list[str],
) -> list[str]:
    """Create ~3,000 orders over 90 days.

    Returns list of order IDs for use by other seed functions.
      - Day 0 (today): no orders

    Most orders include SKU-101 at baseline, but yesterday's orders DON'T
    (due to stockout at 10:00 AM).
    """
    now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = now - timedelta(days=1)

    orders_data = []
    order_items_data = []
    movements_data = []

    # Generate 90 days of orders, backward from yesterday
    for day_offset in range(1, 91):
        order_date = yesterday - timedelta(days=day_offset - 1)

        # Baseline: ~35 orders/day
        if day_offset == 1:
            # Yesterday (the dip day)
            num_orders = 22
        else:
            # All other days
            num_orders = fake.random_int(min=32, max=38)

        for order_num in range(num_orders):
            customer_id, region, _ = fake.random_element(customer_refs)

            order_id = uuid.uuid4()
            order_number = f"ORD-{order_date.strftime('%Y%m%d')}-{order_num:03d}"

            # Determine SKUs for this order
            if day_offset == 1:
                # Yesterday: exclude SKU-101 (it's OOS)
                order_skus = fake.random_sample(
                    elements=[s for s in sku_list if s != "SKU-101"],
                    length=fake.random_int(min=1, max=3),
                )
            else:
                # Normal days: SKU-101 heavily weighted
                if fake.random_int(1, 100) <= 70:
                    # 70% chance to include SKU-101
                    order_skus = [
                        "SKU-101",
                        *fake.random_sample(
                            elements=[s for s in sku_list if s != "SKU-101"],
                            length=fake.random_int(min=0, max=2),
                        ),
                    ]
                else:
                    order_skus = fake.random_sample(
                        elements=sku_list, length=fake.random_int(min=1, max=3)
                    )

            # Calculate order totals
            subtotal = Decimal("0")
            line_items = []

            for sku in order_skus:
                qty = fake.random_int(min=1, max=5)
                unit_price = Decimal(str(round(fake.random.uniform(10, 150), 2)))
                line_total = unit_price * qty
                subtotal += line_total

                line_items.append(
                    {
                        "order_id": order_id,
                        "sku": sku,
                        "quantity": qty,
                        "unit_price": unit_price,
                        "line_total": line_total,
                    }
                )

            discount_amount = Decimal("0")
            shipping_amount = Decimal("9.99")
            tax_amount = (subtotal - discount_amount) * Decimal("0.08")
            total_amount = subtotal - discount_amount + shipping_amount + tax_amount

            # Place order slightly before yesterday@10:00 if it's yesterday
            # (so stockout event at 10:00 doesn't affect it)
            if day_offset == 1:
                placed_at = yesterday + timedelta(hours=8, minutes=fake.random_int(0, 59))
            else:
                placed_at = order_date + timedelta(
                    hours=fake.random_int(0, 23),
                    minutes=fake.random_int(0, 59),
                )

            orders_data.append(
                {
                    "id": order_id,
                    "order_number": order_number,
                    "customer_id": customer_id,
                    "status": fake.random_element(["paid", "shipped", "delivered", "cancelled"]),
                    "region": region,
                    "channel": fake.random_element(["web", "mobile", "marketplace"]),
                    "subtotal": subtotal,
                    "discount_amount": discount_amount,
                    "shipping_amount": shipping_amount,
                    "tax_amount": tax_amount,
                    "total_amount": total_amount,
                    "campaign_id": None,  # Will be set by seed_marketing
                    "placed_at": placed_at,
                    "created_at": datetime.now(UTC),
                }
            )

            # Add order items
            order_items_data.extend(line_items)

            # Create inventory movements for each item sold
            for item in line_items:
                movement_id = uuid.uuid4()
                movements_data.append(
                    {
                        "id": movement_id,
                        "sku": item["sku"],
                        "movement_type": "sale",
                        "quantity_change": -item["quantity"],
                        "quantity_after": 0,  # Will be fixed later during inventory sync
                        "reference_type": "order",
                        "reference_id": str(order_id),
                        "occurred_at": placed_at,
                    }
                )

    # Insert orders in batches (PostgreSQL asyncpg has 32767 parameter limit)
    batch_size = 100
    for i in range(0, len(orders_data), batch_size):
        batch = orders_data[i : i + batch_size]
        stmt = insert(Order).values(batch)
        await session.execute(stmt)
        await session.commit()
    logger.info(
        f"Seeded {len(orders_data)} orders ({(len(orders_data) + batch_size - 1) // batch_size} batches)"
    )

    # Insert order items in batches
    for i in range(0, len(order_items_data), batch_size):
        batch = order_items_data[i : i + batch_size]
        stmt = insert(OrderItem).values(batch)
        await session.execute(stmt)
        await session.commit()
    logger.info(
        f"Seeded {len(order_items_data)} order items ({(len(order_items_data) + batch_size - 1) // batch_size} batches)"
    )

    # Insert movements in batches (note: quantity_after will be approximated)
    for i in range(0, len(movements_data), batch_size):
        batch = movements_data[i : i + batch_size]
        stmt = insert(InventoryMovement).values(batch)
        await session.execute(stmt)
        await session.commit()
    logger.info(
        f"Seeded {len(movements_data)} inventory movements ({(len(movements_data) + batch_size - 1) // batch_size} batches)"
    )

    # Return order IDs for use by other seed functions
    return [str(order["id"]) for order in orders_data]
