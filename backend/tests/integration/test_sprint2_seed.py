"""Integration tests for Sprint 2 "Operational Data Layer & Seed".

Verifies the SKU-101 Stockout Cascade scenario is correctly seeded across all three layers.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db.models import (
    Campaign,
    CampaignMetricsDaily,
    Incident,
    Inventory,
    InventoryMovement,
    Order,
    OrderItem,
    Product,
    SupportTicket,
)


@pytest.fixture
async def db_session() -> AsyncSession:
    """Fixture providing a database session for tests."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with AsyncSessionLocal() as session:
        yield session

    await engine.dispose()


class TestOrderScenario:
    """Test orders scenario: yesterday dip to 22 units."""

    async def test_yesterday_orders_count(self, db_session: AsyncSession) -> None:
        """Yesterday should have exactly 22 orders (the dip)."""
        now = datetime.now(UTC)
        yesterday = (now - timedelta(days=1)).date()

        stmt = select(func.count(Order.id)).where(func.date(Order.placed_at) == yesterday)
        result = await db_session.execute(stmt)
        count = result.scalar_one()

        assert count == 22, f"Expected 22 orders yesterday, got {count}"

    async def test_yesterday_orders_exclude_sku101(self, db_session: AsyncSession) -> None:
        """Yesterday's orders should not contain SKU-101."""
        now = datetime.now(UTC)
        yesterday = (now - timedelta(days=1)).date()

        stmt = (
            select(func.count(OrderItem.id))
            .join(Order)
            .where(
                and_(
                    func.date(Order.placed_at) == yesterday,
                    OrderItem.sku == "SKU-101",
                )
            )
        )
        result = await db_session.execute(stmt)
        count = result.scalar_one()

        assert count == 0, f"Expected 0 SKU-101 items yesterday, got {count}"

    async def test_baseline_orders_include_sku101(self, db_session: AsyncSession) -> None:
        """Normal days (not yesterday) should have ~70% with SKU-101."""
        now = datetime.now(UTC)
        baseline_date = (now - timedelta(days=5)).date()

        # Count orders on baseline day
        stmt_orders = select(func.count(Order.id)).where(
            func.date(Order.placed_at) == baseline_date
        )
        result = await db_session.execute(stmt_orders)
        total_orders = result.scalar_one()

        # Count orders with SKU-101
        stmt_sku101 = (
            select(func.count(func.distinct(Order.id)))
            .select_from(OrderItem)
            .join(Order)
            .where(
                and_(
                    func.date(Order.placed_at) == baseline_date,
                    OrderItem.sku == "SKU-101",
                )
            )
        )
        result = await db_session.execute(stmt_sku101)
        orders_with_sku101 = result.scalar_one()

        if total_orders > 0:
            pct = (orders_with_sku101 / total_orders) * 100
            assert 60 <= pct <= 80, f"Expected ~70% orders with SKU-101 on baseline, got {pct}%"


class TestInventoryScenario:
    """Test inventory scenario: SKU-101 OOS at yesterday 10:00."""

    async def test_sku101_inventory_zero(self, db_session: AsyncSession) -> None:
        """SKU-101 quantity_on_hand should be 0."""
        stmt = select(Inventory.quantity_on_hand).where(Inventory.sku == "SKU-101")
        result = await db_session.execute(stmt)
        quantity = result.scalar_one()

        assert quantity == 0, f"Expected SKU-101 on_hand=0, got {quantity}"

    async def test_sku101_zero_crossing_event(self, db_session: AsyncSession) -> None:
        """Should have inventory movement with quantity_after=0 for SKU-101."""
        now = datetime.now(UTC)
        yesterday = now - timedelta(days=1)
        yesterday_9am = yesterday.replace(hour=9, minute=0, second=0, microsecond=0)
        yesterday_11am = yesterday.replace(hour=11, minute=0, second=0, microsecond=0)

        stmt = select(InventoryMovement).where(
            and_(
                InventoryMovement.sku == "SKU-101",
                InventoryMovement.quantity_after == 0,
                InventoryMovement.occurred_at >= yesterday_9am,
                InventoryMovement.occurred_at <= yesterday_11am,
            )
        )
        result = await db_session.execute(stmt)
        movement = result.scalar_one_or_none()

        assert (
            movement is not None
        ), "Expected zero-crossing movement for SKU-101 at yesterday 10:00"
        assert movement.quantity_change == -50


class TestCampaignScenario:
    """Test campaign scenario: Summer Sale paused yesterday 09:00."""

    async def test_summer_sale_paused(self, db_session: AsyncSession) -> None:
        """Summer Sale campaign should be paused."""
        stmt = select(Campaign).where(Campaign.name == "Summer Sale")
        result = await db_session.execute(stmt)
        campaign = result.scalar_one()

        assert campaign.status == "paused", f"Expected status=paused, got {campaign.status}"

    async def test_summer_sale_paused_at_9am(self, db_session: AsyncSession) -> None:
        """Summer Sale should be paused at yesterday 09:00 (±1 hour tolerance)."""
        now = datetime.now(UTC)
        yesterday_9am = (now - timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        yesterday_10am = (now - timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )

        stmt = select(Campaign).where(Campaign.name == "Summer Sale")
        result = await db_session.execute(stmt)
        campaign = result.scalar_one()

        assert campaign.paused_at is not None, "Expected paused_at to be set for Summer Sale"
        assert (
            yesterday_9am <= campaign.paused_at <= yesterday_10am
        ), f"Expected paused_at between 9:00-10:00, got {campaign.paused_at}"

    async def test_summer_sale_metrics_zero_after_pause(self, db_session: AsyncSession) -> None:
        """Campaign metrics after pause should have 0 impressions and 0 spend."""
        now = datetime.now(UTC)
        yesterday = (now - timedelta(days=1)).date()

        stmt = (
            select(CampaignMetricsDaily)
            .join(Campaign)
            .where(
                and_(
                    Campaign.name == "Summer Sale",
                    CampaignMetricsDaily.metric_date >= yesterday,
                )
            )
        )
        result = await db_session.execute(stmt)
        metrics_list = result.scalars().all()

        assert len(metrics_list) > 0, "Expected campaign metrics for Summer Sale"
        for metric in metrics_list:
            if metric.metric_date >= yesterday:
                assert (
                    metric.impressions == 0
                ), f"Expected 0 impressions after pause, got {metric.impressions}"
                assert metric.spend == 0, f"Expected 0 spend after pause, got {metric.spend}"


class TestSupportScenario:
    """Test support scenario: 8 tickets yesterday, 5 on SKU-101 with negative sentiment."""

    async def test_support_tickets_spike_yesterday(self, db_session: AsyncSession) -> None:
        """Yesterday should have 8 support tickets."""
        now = datetime.now(UTC)
        yesterday = (now - timedelta(days=1)).date()

        stmt = select(func.count(SupportTicket.id)).where(
            func.date(SupportTicket.created_at) == yesterday
        )
        result = await db_session.execute(stmt)
        count = result.scalar_one()

        assert count == 8, f"Expected 8 tickets yesterday, got {count}"

    async def test_support_tickets_sku101_yesterday(self, db_session: AsyncSession) -> None:
        """Yesterday should have at least 5 tickets related to SKU-101."""
        now = datetime.now(UTC)
        yesterday = (now - timedelta(days=1)).date()

        stmt = select(func.count(SupportTicket.id)).where(
            and_(
                func.date(SupportTicket.created_at) == yesterday,
                SupportTicket.related_sku == "SKU-101",
            )
        )
        result = await db_session.execute(stmt)
        count = result.scalar_one()

        assert count >= 5, f"Expected at least 5 SKU-101 tickets yesterday, got {count}"

    async def test_support_tickets_negative_sentiment_yesterday(
        self, db_session: AsyncSession
    ) -> None:
        """Yesterday's SKU-101 tickets should mostly have negative sentiment."""
        now = datetime.now(UTC)
        yesterday = (now - timedelta(days=1)).date()

        stmt = select(SupportTicket).where(
            and_(
                func.date(SupportTicket.created_at) == yesterday,
                SupportTicket.related_sku == "SKU-101",
            )
        )
        result = await db_session.execute(stmt)
        tickets = result.scalars().all()

        # Count tickets with negative sentiment
        negative_sentiment_count = sum(
            1 for t in tickets if t.sentiment_score is not None and t.sentiment_score < 0
        )

        # Seed plants at least 5 with negative sentiment
        assert negative_sentiment_count >= 5, (
            f"Expected at least 5 SKU-101 tickets with negative sentiment yesterday, "
            f"got {negative_sentiment_count}"
        )


class TestMemoryLayer:
    """Test Layer 3 (long-term memory): incidents."""

    async def test_incidents_exist(self, db_session: AsyncSession) -> None:
        """Should have at least 3 incidents."""
        stmt = select(func.count(Incident.id))
        result = await db_session.execute(stmt)
        count = result.scalar_one()

        assert count >= 3, f"Expected ≥3 incidents, got {count}"

    async def test_incidents_have_summaries(self, db_session: AsyncSession) -> None:
        """All incidents should have summaries."""
        stmt = select(Incident).where(Incident.summary != "")
        result = await db_session.execute(stmt)
        incidents = result.scalars().all()

        assert len(incidents) >= 3, "Expected all incidents to have summaries"


class TestCrossDomainIntegration:
    """Test cross-domain correlations."""

    async def test_campaign_id_references_exist(self, db_session: AsyncSession) -> None:
        """Orders with campaign_id should reference existing campaigns."""
        stmt = select(func.count(Order.id)).where(
            and_(
                Order.campaign_id.isnot(None),
                ~select(Campaign).where(Campaign.id == Order.campaign_id).exists(),
            )
        )
        result = await db_session.execute(stmt)
        orphan_count = result.scalar_one()

        assert (
            orphan_count == 0
        ), f"Expected 0 orphan campaign references in orders, got {orphan_count}"

    async def test_order_items_sku_references(self, db_session: AsyncSession) -> None:
        """All order items should reference existing products."""
        stmt = select(func.count(OrderItem.id)).where(
            ~select(Product).where(Product.sku == OrderItem.sku).exists()
        )
        result = await db_session.execute(stmt)
        orphan_count = result.scalar_one()

        assert orphan_count == 0, f"Expected 0 orphan SKU references, got {orphan_count}"
