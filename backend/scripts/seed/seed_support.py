"""Seed support tickets and returns.

Key signals:
  - Yesterday: 8 tickets (vs baseline ~2) with 5 related to SKU-101
  - 5 tickets with negative sentiment (-0.8 to -0.5)
  - Returns: steady with small bump for SKU-101

This demonstrates the Support-Sales-Inventory correlation.
"""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Return, SupportTicket

from ._faker import fake

logger = logging.getLogger("seed_support")


async def seed_support_tickets(
    session: AsyncSession,
    customer_refs: list[tuple],
) -> None:
    """Create ~250 support tickets over 90 days.

    Key signal: Yesterday has 8 tickets (baseline ~2), with 5 on SKU-101,
    all with negative sentiment.
    """

    now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = now - timedelta(days=1)

    categories = [
        "shipping",
        "product_quality",
        "payment",
        "refund_request",
        "other",
    ]

    tickets_data = []
    ticket_num = 1000  # Start at 1000 to avoid conflicts

    # Generate tickets over 90 days
    for day_offset in range(1, 91):
        ticket_date = yesterday - timedelta(days=day_offset - 1)

        # Baseline: ~2 tickets/day
        if day_offset == 1:
            # Yesterday (spike day)
            num_tickets = 8
        else:
            num_tickets = fake.random_int(min=1, max=3)

        for ticket_ord in range(num_tickets):
            customer_id = fake.random_element(customer_refs)[0]

            category = fake.random_element(categories)
            priority = fake.random_element(["low", "medium", "high"])

            # Yesterday: force SKU-101 and negative sentiment
            if day_offset == 1 and ticket_ord < 5:
                related_sku = "SKU-101"
                sentiment = Decimal(str(round(fake.random.uniform(-0.95, -0.75), 2)))
                subject = f"Issue with Aurora Earbuds - {category}"
            else:
                related_sku = (
                    fake.random_element(["SKU-101", "SKU-102", None, None, None])
                    if fake.boolean()
                    else None
                )
                sentiment = (
                    Decimal(str(round(fake.random.uniform(-0.9, 0.9), 2)))
                    if fake.boolean()
                    else None
                )
                subject = fake.sentence(nb_words=5)

            status = fake.random_element(["open", "in_progress", "resolved", "closed"])
            resolved_at = (
                ticket_date + timedelta(days=fake.random_int(1, 5))
                if status in ["resolved", "closed"]
                else None
            )

            tickets_data.append(
                {
                    "ticket_number": f"TKT-2024-{ticket_num:05d}",
                    "customer_id": customer_id,
                    "order_id": None,  # Could join with orders if needed
                    "related_sku": related_sku,
                    "category": category,
                    "priority": priority,
                    "status": status,
                    "subject": subject,
                    "description": fake.paragraph(nb_sentences=3),
                    "sentiment_score": sentiment,
                    "created_at": ticket_date
                    + timedelta(
                        hours=fake.random_int(0, 23),
                        minutes=fake.random_int(0, 59),
                    ),
                    "resolved_at": resolved_at,
                }
            )
            ticket_num += 1

    batch_size = 50
    for i in range(0, len(tickets_data), batch_size):
        batch = tickets_data[i : i + batch_size]
        stmt = insert(SupportTicket).values(batch)
        await session.execute(stmt)
        await session.commit()
    logger.info(f"Seeded {len(tickets_data)} support tickets")
    logger.info("  - Yesterday spike: 8 tickets (5 on SKU-101, negative sentiment)")


async def seed_returns(session: AsyncSession, order_ids: list[str]) -> None:
    """Create ~120 returns over 90 days, linked to actual orders.

    Steady baseline with a small bump tied to SKU-101 quality issues.
    """

    now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = now - timedelta(days=1)

    reasons = [
        "damaged",
        "wrong_item",
        "not_as_described",
        "changed_mind",
    ]

    returns_data = []

    # Generate returns over 90 days
    for day_offset in range(1, 91):
        return_date = yesterday - timedelta(days=day_offset - 1)

        # Baseline: ~1.3 returns/day
        if day_offset == 1:
            # Small bump yesterday
            num_returns = fake.random_int(min=2, max=3)
        else:
            num_returns = 1 if fake.boolean(chance_of_getting_true=30) else 0

        for _ in range(num_returns):
            # Most returns are SKU-101 or random
            if fake.boolean(chance_of_getting_true=60):
                sku = "SKU-101"
            else:
                sku = fake.random_element(["SKU-102", "SKU-103", "SKU-104", "SKU-105", "SKU-101"])

            reason = fake.random_element(reasons)
            status = fake.random_element(["requested", "approved", "completed", "rejected"])
            completed_at = (
                return_date + timedelta(days=fake.random_int(1, 10))
                if status in ["completed", "approved"]
                else None
            )

            returns_data.append(
                {
                    "order_id": fake.random_element(order_ids),  # Use actual order ID
                    "sku": sku,
                    "quantity": fake.random_int(min=1, max=3),
                    "reason": reason,
                    "refund_amount": Decimal(str(round(fake.random.uniform(20, 200), 2))),
                    "status": status,
                    "requested_at": return_date,
                    "completed_at": completed_at,
                }
            )

    batch_size = 50
    for i in range(0, len(returns_data), batch_size):
        batch = returns_data[i : i + batch_size]
        stmt = insert(Return).values(batch)
        await session.execute(stmt)
        await session.commit()
    logger.info(f"Seeded {len(returns_data)} returns")
