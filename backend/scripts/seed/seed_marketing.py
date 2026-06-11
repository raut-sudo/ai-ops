"""Seed marketing campaigns and daily metrics.

Key campaign:
  - "Summer Sale": channel=google_ads, status=paused (paused_at=yesterday 09:00),
    target_skus includes SKU-101, budget=$1000, discount=15%.

Other campaigns:
  - 5 other campaigns with varying statuses and metrics

Daily metrics for all campaigns over 90 days.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Campaign, CampaignMetricsDaily

from ._faker import fake

logger = logging.getLogger("seed_marketing")


async def seed_campaigns(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Create 6 campaigns. Return campaign_id mapping for order attribution."""

    now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = now - timedelta(days=1)

    campaigns_data = [
        {
            "id": uuid.uuid4(),
            "name": "Summer Sale",
            "channel": "google_ads",
            "status": "paused",
            "budget_total": Decimal("1000.00"),
            "budget_spent": Decimal("750.00"),
            "target_skus": ["SKU-101", "SKU-102", "SKU-103"],
            "discount_percent": Decimal("15.00"),
            "started_at": yesterday - timedelta(days=30),
            "ends_at": yesterday + timedelta(days=30),
            "paused_at": yesterday + timedelta(hours=9, minutes=0),  # Paused at 09:00
            "created_at": datetime.now(UTC),
        },
        {
            "id": uuid.uuid4(),
            "name": "Flash Deal Friday",
            "channel": "meta",
            "status": "active",
            "budget_total": Decimal("500.00"),
            "budget_spent": Decimal("480.00"),
            "target_skus": ["SKU-104", "SKU-105"],
            "discount_percent": Decimal("10.00"),
            "started_at": yesterday - timedelta(days=14),
            "ends_at": yesterday + timedelta(days=14),
            "paused_at": None,
            "created_at": datetime.now(UTC),
        },
        {
            "id": uuid.uuid4(),
            "name": "Email Blast - Spring",
            "channel": "email",
            "status": "completed",
            "budget_total": Decimal("200.00"),
            "budget_spent": Decimal("200.00"),
            "target_skus": None,
            "discount_percent": None,
            "started_at": yesterday - timedelta(days=60),
            "ends_at": yesterday - timedelta(days=59),
            "paused_at": None,
            "created_at": datetime.now(UTC),
        },
        {
            "id": uuid.uuid4(),
            "name": "TikTok Influencer",
            "channel": "tiktok",
            "status": "active",
            "budget_total": Decimal("800.00"),
            "budget_spent": Decimal("600.00"),
            "target_skus": ["SKU-101", "SKU-104"],
            "discount_percent": Decimal("20.00"),
            "started_at": yesterday - timedelta(days=10),
            "ends_at": yesterday + timedelta(days=20),
            "paused_at": None,
            "created_at": datetime.now(UTC),
        },
        {
            "id": uuid.uuid4(),
            "name": "Amazon Sponsored Ads",
            "channel": "marketplace",
            "status": "draft",
            "budget_total": Decimal("300.00"),
            "budget_spent": Decimal("0.00"),
            "target_skus": None,
            "discount_percent": None,
            "started_at": None,
            "ends_at": None,
            "paused_at": None,
            "created_at": datetime.now(UTC),
        },
        {
            "id": uuid.uuid4(),
            "name": "Holiday Bundle",
            "channel": "google_ads",
            "status": "completed",
            "budget_total": Decimal("1200.00"),
            "budget_spent": Decimal("1200.00"),
            "target_skus": None,
            "discount_percent": Decimal("25.00"),
            "started_at": yesterday - timedelta(days=90),
            "ends_at": yesterday - timedelta(days=89),
            "paused_at": None,
            "created_at": datetime.now(UTC),
        },
    ]

    stmt = insert(Campaign).values(campaigns_data).returning(Campaign.id, Campaign.name)
    result = await session.execute(stmt)
    await session.commit()

    campaign_map = {}
    for row in result:
        campaign_map[row[1]] = row[0]

    logger.info(f"Seeded {len(campaigns_data)} campaigns")
    logger.info("  - Summer Sale: paused at yesterday 09:00 (THE KEY SIGNAL)")
    return campaign_map


async def seed_campaign_metrics(session: AsyncSession, campaigns_data: list[dict]) -> None:
    """Create daily metrics for all campaigns over 90 days.

    Metrics are synthetic but realistic. Summer Sale has high metrics
    until paused yesterday.
    """

    now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    metrics_data = []

    for campaign in campaigns_data:
        campaign_id = campaign.get("id")
        if not campaign_id:
            continue

        campaign_name = campaign.get("name", "")

        # Only create metrics for active/completed campaigns
        if campaign["status"] not in ["active", "completed", "paused"]:
            continue

        paused_at = campaign.get("paused_at")
        started_at = campaign.get("started_at")
        ended_at = campaign.get("ends_at")

        # Generate metrics for each day
        for day_offset in range(90, -1, -1):
            metric_date = (now - timedelta(days=day_offset)).date()

            # Skip if outside campaign date range
            if started_at and metric_date < started_at.date():
                continue
            if ended_at and metric_date > ended_at.date():
                continue

            # Determine if this metric date is after pause
            is_after_pause = paused_at and metric_date >= paused_at.date()

            if campaign_name == "Summer Sale" and is_after_pause:
                # After pause: zero metrics
                impressions = 0
                clicks = 0
                conversions = 0
                spend = Decimal("0.00")
                attributed_revenue = Decimal("0.00")
            else:
                # Normal metrics
                impressions = fake.random_int(min=500, max=5000)
                clicks = max(1, int(impressions * fake.random.uniform(0.01, 0.05)))
                conversions = max(1, int(clicks * fake.random.uniform(0.02, 0.15)))
                spend = Decimal(str(round(fake.random.uniform(10, 100), 2)))
                attributed_revenue = Decimal(str(round(conversions * 50, 2)))

            metrics_data.append(
                {
                    "campaign_id": campaign_id,
                    "metric_date": metric_date,
                    "impressions": impressions,
                    "clicks": clicks,
                    "conversions": conversions,
                    "spend": spend,
                    "attributed_revenue": attributed_revenue,
                }
            )

    stmt = insert(CampaignMetricsDaily).values(metrics_data)
    await session.execute(stmt)
    await session.commit()
    logger.info(f"Seeded {len(metrics_data)} daily campaign metrics")
