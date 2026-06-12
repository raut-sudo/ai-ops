"""Seed products and inventory baselines.

Creates 30 products:
  - SKU-101: "Aurora Wireless Earbuds" (protagonist of the scenario)
  - 5 related products
  - 24 distractors

All start with quantity_on_hand > 0 EXCEPT SKU-101 (will be zeroed by inventory_events).
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Inventory, Product

from ._faker import fake

logger = logging.getLogger("seed_catalog")


async def seed_products(session: AsyncSession) -> dict[str, str]:
    """Create products. Return sku_name mapping for later references."""

    # Protagonist product (SKU-101)
    products_data = [
        {
            "sku": "SKU-101",
            "name": "Aurora Wireless Earbuds",
            "category": "Electronics",
            "subcategory": "Audio",
            "brand": "TechSound",
            "unit_price": Decimal("79.99"),
            "cost_price": Decimal("32.00"),
            "is_active": True,
        },
        # Related products (same category, complement the scenario)
        {
            "sku": "SKU-102",
            "name": "Wireless Charging Case for Aurora",
            "category": "Electronics",
            "subcategory": "Accessories",
            "brand": "TechSound",
            "unit_price": Decimal("29.99"),
            "cost_price": Decimal("10.00"),
            "is_active": True,
        },
        {
            "sku": "SKU-103",
            "name": "Aurora Screen Protector",
            "category": "Electronics",
            "subcategory": "Accessories",
            "brand": "GenericTech",
            "unit_price": Decimal("9.99"),
            "cost_price": Decimal("2.00"),
            "is_active": True,
        },
        {
            "sku": "SKU-104",
            "name": "Premium Silicone Earbud Cases",
            "category": "Electronics",
            "subcategory": "Accessories",
            "brand": "CaseLux",
            "unit_price": Decimal("19.99"),
            "cost_price": Decimal("5.00"),
            "is_active": True,
        },
        {
            "sku": "SKU-105",
            "name": "Bluetooth Adapter Cable",
            "category": "Electronics",
            "subcategory": "Cables",
            "brand": "GenericTech",
            "unit_price": Decimal("14.99"),
            "cost_price": Decimal("4.00"),
            "is_active": True,
        },
        {
            "sku": "SKU-106",
            "name": "Wireless Charging Pad",
            "category": "Electronics",
            "subcategory": "Charging",
            "brand": "ChargeFast",
            "unit_price": Decimal("34.99"),
            "cost_price": Decimal("12.00"),
            "is_active": True,
        },
    ]

    # Add 24 distractors (other products not in the main narrative)
    for i in range(107, 131):
        products_data.append(
            {
                "sku": f"SKU-{i}",
                "name": f"Product {i} ({fake.word()})",
                "category": fake.random_element(
                    ["Electronics", "Fashion", "Home", "Sports", "Books"]
                ),
                "subcategory": fake.word().capitalize(),
                "brand": fake.company(),
                "unit_price": Decimal(str(round(fake.random.uniform(5, 200), 2))),
                "cost_price": Decimal(str(round(fake.random.uniform(1, 100), 2))),
                "is_active": fake.boolean(chance_of_getting_true=90),
            }
        )

    # Insert all products
    stmt = insert(Product).values(products_data)
    await session.execute(stmt)
    await session.commit()
    logger.info(f"Seeded {len(products_data)} products")

    # Return sku_name mapping for reference
    return {p["sku"]: p["name"] for p in products_data}


async def seed_inventory(session: AsyncSession, sku_name_map: dict[str, str]) -> None:
    """Create inventory rows for all products.

    SKU-101 starts with quantity_on_hand > 0 but will be zeroed by seed_inventory_events.
    All others start well-stocked.
    """
    inventory_data = []

    for sku in sku_name_map.keys():
        if sku == "SKU-101":
            # Protagonist: start fully stocked; will be depleted by events
            quantity_on_hand = 50
            reorder_point = 10
            reorder_quantity = 200
        else:
            # Others: healthy stock
            quantity_on_hand = fake.random_int(min=20, max=500)
            reorder_point = fake.random_int(min=5, max=20)
            reorder_quantity = fake.random_int(min=50, max=300)

        inventory_data.append(
            {
                "sku": sku,
                "warehouse_id": "WH-MAIN",
                "quantity_on_hand": quantity_on_hand,
                "quantity_reserved": 0,
                "reorder_point": reorder_point,
                "reorder_quantity": reorder_quantity,
                "last_restocked_at": datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
                "updated_at": datetime.now(UTC),
            }
        )

    stmt = insert(Inventory).values(inventory_data)
    await session.execute(stmt)
    await session.commit()
    logger.info(f"Seeded {len(inventory_data)} inventory rows")
