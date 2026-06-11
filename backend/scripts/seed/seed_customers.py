"""Seed customers across 4 regions and 3 segments."""

import logging
from datetime import UTC, datetime

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Customer

from ._faker import fake

logger = logging.getLogger("seed_customers")


async def seed_customers(session: AsyncSession) -> list[tuple]:
    """Create 200 customers spread across regions and segments.

    Returns list of (customer_id, region, segment) tuples for order seeding.
    """
    regions = ["NA", "EU", "APAC", "LATAM"]
    segments = ["new", "returning", "vip"]

    customers_data = []
    customer_refs = []

    for i in range(200):
        region = fake.random_element(regions)
        segment = fake.random_element(segments)

        email = f"customer_{i:03d}_{fake.word()}@example.com"
        customer = {
            "email": email,
            "region": region,
            "customer_segment": segment,
            "created_at": datetime.now(UTC),
        }
        customers_data.append(customer)
        # Return customer dict for reference (will get id from DB insert)
        customer_refs.append((email, region, segment))

    stmt = insert(Customer).values(customers_data).returning(Customer.id, Customer.email)
    result = await session.execute(stmt)
    await session.commit()

    # Map emails to IDs
    email_to_id = {}
    for row in result:
        email_to_id[row[1]] = row[0]

    # Build final refs with actual IDs
    final_refs = [(email_to_id[ref[0]], ref[1], ref[2]) for ref in customer_refs]

    logger.info(f"Seeded {len(customers_data)} customers")
    return final_refs
