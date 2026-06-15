from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

from .base import resolve_period, tool_retry


def _derive_rates(impressions: int, clicks: int, spend: float, attributed_revenue: float) -> dict:
    ctr = (clicks / impressions * 100.0) if impressions > 0 else 0.0
    roas = (attributed_revenue / spend) if spend > 0 else 0.0
    revenue_per_click = (attributed_revenue / clicks) if clicks > 0 else 0.0
    return {
        "ctr_pct": ctr,
        "roas": roas,
        "revenue_per_click": revenue_per_click,
    }


@tool_retry
async def _get_campaign_performance_impl(
    session: AsyncSession,
    period: str,
    campaign_id: str | None = None,
) -> list[dict]:
    start, end = resolve_period(period)

    where = ["cmd.metric_date >= :start_date", "cmd.metric_date < :end_date"]
    params: dict[str, object] = {
        "start_date": start.date(),
        "end_date": end.date(),
    }
    if campaign_id:
        where.append("c.id = :campaign_id")
        params["campaign_id"] = campaign_id

    sql = text(
        """
        SELECT
            c.id,
            c.name,
            c.status,
            COALESCE(SUM(cmd.impressions), 0) AS impressions,
            COALESCE(SUM(cmd.clicks), 0) AS clicks,
            COALESCE(SUM(cmd.conversions), 0) AS conversions,
            COALESCE(SUM(cmd.spend), 0) AS spend,
            COALESCE(SUM(cmd.attributed_revenue), 0) AS attributed_revenue
        FROM campaigns c
        JOIN campaign_metrics_daily cmd ON cmd.campaign_id = c.id
        WHERE """
        + " AND ".join(where)
        + " GROUP BY c.id, c.name, c.status ORDER BY attributed_revenue DESC"
    )
    rows = (await session.execute(sql, params)).all()

    results: list[dict] = []
    for row in rows:
        spend = float(row.spend)
        attributed_revenue = float(row.attributed_revenue)
        rates = _derive_rates(
            int(row.impressions),
            int(row.clicks),
            spend,
            attributed_revenue,
        )
        results.append(
            {
                "campaign_id": str(row.id),
                "name": row.name,
                "status": row.status,
                "impressions": int(row.impressions),
                "clicks": int(row.clicks),
                "conversions": int(row.conversions),
                "spend": spend,
                "attributed_revenue": attributed_revenue,
                "ctr_pct": rates["ctr_pct"],
                "roas": rates["roas"],
                "revenue_per_click": rates["revenue_per_click"],
            }
        )
    return results


@tool_retry
async def _analyze_marketing_impl(session: AsyncSession, period: str) -> dict:
    campaigns = await _get_campaign_performance_impl(session, period)

    total_spend = sum(c["spend"] for c in campaigns)
    total_revenue = sum(c["attributed_revenue"] for c in campaigns)
    total_impressions = sum(c["impressions"] for c in campaigns)
    total_clicks = sum(c["clicks"] for c in campaigns)
    total_conversions = sum(c["conversions"] for c in campaigns)

    rates = _derive_rates(total_impressions, total_clicks, total_spend, total_revenue)

    return {
        "period": period,
        "campaign_count": len(campaigns),
        "total_spend": total_spend,
        "total_attributed_revenue": total_revenue,
        "total_impressions": total_impressions,
        "total_clicks": total_clicks,
        "total_conversions": total_conversions,
        "overall_roas": rates["roas"],
        "overall_ctr_pct": rates["ctr_pct"],
        "overall_revenue_per_click": rates["revenue_per_click"],
    }


async def analyze_marketing(period: str) -> dict:
    async with get_session() as session:
        return await _analyze_marketing_impl(session, period)


@tool_retry
async def _get_underperforming_campaigns_impl(
    session: AsyncSession,
    period: str,
    roas_threshold: float,
) -> list[dict]:
    campaigns = await _get_campaign_performance_impl(session, period)
    return [c for c in campaigns if c["roas"] < roas_threshold]


async def get_underperforming_campaigns(period: str, roas_threshold: float = 1.0) -> list[dict]:
    async with get_session() as session:
        return await _get_underperforming_campaigns_impl(
            session,
            period,
            roas_threshold=roas_threshold,
        )


@tool_retry
async def _get_campaigns_for_sku_impl(session: AsyncSession, sku: str) -> list[dict]:
    sql = text(
        """
        SELECT id, name, status, channel, paused_at, started_at, ends_at, discount_percent
        FROM campaigns
        WHERE :sku = ANY(target_skus)
          AND status IN ('active', 'paused')
        ORDER BY started_at DESC NULLS LAST, created_at DESC
        """
    )
    rows = (await session.execute(sql, {"sku": sku})).all()

    return [
        {
            "campaign_id": str(row.id),
            "name": row.name,
            "status": row.status,
            "channel": row.channel,
            "paused_at": row.paused_at,
            "started_at": row.started_at,
            "ends_at": row.ends_at,
            "discount_percent": float(row.discount_percent)
            if row.discount_percent is not None
            else None,
        }
        for row in rows
    ]


async def get_campaigns_for_sku(sku: str) -> list[dict]:
    async with get_session() as session:
        return await _get_campaigns_for_sku_impl(session, sku)


@tool_retry
async def _get_unpromoted_top_products_impl(
    session: AsyncSession, period: str, limit: int = 10
) -> list[dict]:
    start, end = resolve_period(period)

    sql = text(
        """
        SELECT
            oi.sku,
            p.name,
            COALESCE(SUM(oi.quantity), 0)   AS units_sold,
            COALESCE(SUM(oi.line_total), 0) AS revenue
        FROM order_items oi
        JOIN orders o  ON o.id  = oi.order_id
        JOIN products p ON p.sku = oi.sku
        WHERE o.placed_at >= :start
          AND o.placed_at <  :end
          AND o.status NOT IN ('cancelled')
          AND NOT EXISTS (
              SELECT 1 FROM campaigns c
              WHERE oi.sku = ANY(c.target_skus)
                AND c.status IN ('active', 'paused')
          )
        GROUP BY oi.sku, p.name
        ORDER BY revenue DESC
        LIMIT :limit
        """
    )
    rows = (await session.execute(sql, {"start": start, "end": end, "limit": limit})).all()

    return [
        {
            "sku": row.sku,
            "name": row.name,
            "units_sold": int(row.units_sold),
            "revenue": float(row.revenue),
        }
        for row in rows
    ]


async def get_unpromoted_top_products(period: str, limit: int = 10) -> list[dict]:
    async with get_session() as session:
        return await _get_unpromoted_top_products_impl(session, period, limit=limit)


async def get_campaign_performance(period: str) -> list[dict]:
    """Public wrapper around _get_campaign_performance_impl."""
    async with get_session() as session:
        return await _get_campaign_performance_impl(session, period)


async def get_active_campaigns_for_sku(sku: str) -> list[dict]:
    """Return only active (non-paused) campaigns targeting the given SKU."""
    rows = await get_campaigns_for_sku(sku)
    return [r for r in rows if r["status"] == "active"]


@tool_retry
async def _get_campaign_by_channel_impl(session: AsyncSession, period: str) -> list[dict]:
    start, end = resolve_period(period)
    sql = text(
        """
        SELECT c.channel,
               COUNT(DISTINCT c.id)                              AS campaign_count,
               COALESCE(SUM(m.impressions), 0)                  AS impressions,
               COALESCE(SUM(m.clicks), 0)                       AS clicks,
               COALESCE(SUM(m.conversions), 0)                  AS conversions,
               COALESCE(SUM(m.spend), 0)                        AS spend,
               COALESCE(SUM(m.attributed_revenue), 0)           AS attributed_revenue,
               CASE WHEN COALESCE(SUM(m.spend), 0) > 0
                    THEN ROUND(COALESCE(SUM(m.attributed_revenue), 0) / SUM(m.spend), 4)
                    ELSE 0
               END AS roas
        FROM campaigns c
        JOIN campaign_metrics_daily m ON m.campaign_id = c.id
        WHERE m.metric_date >= :start
          AND m.metric_date < :end
        GROUP BY c.channel
        ORDER BY roas DESC
        """
    )
    rows = (await session.execute(sql, {"start": start, "end": end})).all()
    return [
        {
            "channel": row.channel,
            "campaign_count": int(row.campaign_count),
            "impressions": int(row.impressions),
            "clicks": int(row.clicks),
            "conversions": int(row.conversions),
            "spend": float(row.spend),
            "attributed_revenue": float(row.attributed_revenue),
            "roas": float(row.roas),
        }
        for row in rows
    ]


async def get_campaign_by_channel(period: str) -> list[dict]:
    """Return aggregated campaign metrics (spend, revenue, ROAS) grouped by channel."""
    async with get_session() as session:
        return await _get_campaign_by_channel_impl(session, period)


@tool_retry
async def _get_campaigns_near_budget_exhaustion_impl(
    session: AsyncSession, pct_threshold: float
) -> list[dict]:
    sql = text(
        """
        SELECT id::text AS campaign_id, name, channel, status,
               budget_total, budget_spent, ends_at,
               ROUND(budget_spent / NULLIF(budget_total, 0) * 100, 1) AS pct_spent
        FROM campaigns
        WHERE status = 'active'
          AND budget_total > 0
          AND (budget_spent / budget_total * 100) >= :threshold
        ORDER BY pct_spent DESC
        """
    )
    rows = (await session.execute(sql, {"threshold": pct_threshold})).all()
    return [
        {
            "campaign_id": row.campaign_id,
            "name": row.name,
            "channel": row.channel,
            "budget_total": float(row.budget_total),
            "budget_spent": float(row.budget_spent),
            "pct_spent": float(row.pct_spent),
            "ends_at": row.ends_at,
        }
        for row in rows
    ]


async def get_campaigns_near_budget_exhaustion(pct_threshold: float = 90.0) -> list[dict]:
    """Return active campaigns that have spent >= pct_threshold% of their total budget."""
    async with get_session() as session:
        return await _get_campaigns_near_budget_exhaustion_impl(session, pct_threshold)


@tool_retry
async def _get_campaign_daily_trend_impl(
    session: AsyncSession, campaign_id: str, period: str
) -> list[dict]:
    start, end = resolve_period(period)
    sql = text(
        """
        SELECT metric_date,
               impressions, clicks, conversions,
               spend, attributed_revenue,
               CASE WHEN spend > 0
                    THEN ROUND(attributed_revenue / spend, 4)
                    ELSE 0
               END AS roas
        FROM campaign_metrics_daily
        WHERE campaign_id = :campaign_id
          AND metric_date >= :start
          AND metric_date < :end
        ORDER BY metric_date
        """
    )
    rows = (
        await session.execute(sql, {"campaign_id": campaign_id, "start": start, "end": end})
    ).all()
    return [
        {
            "date": str(row.metric_date),
            "impressions": int(row.impressions),
            "clicks": int(row.clicks),
            "conversions": int(row.conversions),
            "spend": float(row.spend),
            "attributed_revenue": float(row.attributed_revenue),
            "roas": float(row.roas),
        }
        for row in rows
    ]


async def get_campaign_daily_trend(campaign_id: str, period: str) -> list[dict]:
    """Return day-by-day metrics for a specific campaign — spot acceleration or deceleration."""
    async with get_session() as session:
        return await _get_campaign_daily_trend_impl(session, campaign_id, period)


@tool_retry
async def _get_discount_impact_impl(session: AsyncSession, period: str) -> dict:
    start, end = resolve_period(period)
    sql = text(
        """
        SELECT
            COUNT(*) FILTER (WHERE campaign_id IS NOT NULL)                          AS promoted_orders,
            COUNT(*) FILTER (WHERE campaign_id IS NULL)                              AS organic_orders,
            COALESCE(SUM(total_amount)    FILTER (WHERE campaign_id IS NOT NULL), 0) AS promoted_revenue,
            COALESCE(SUM(total_amount)    FILTER (WHERE campaign_id IS NULL), 0)     AS organic_revenue,
            COALESCE(SUM(discount_amount), 0)                                        AS total_discount_given,
            COALESCE(SUM(discount_amount) FILTER (WHERE campaign_id IS NOT NULL), 0) AS campaign_discount_given
        FROM orders
        WHERE placed_at >= :start
          AND placed_at < :end
          AND status NOT IN ('cancelled')
        """
    )
    row = (await session.execute(sql, {"start": start, "end": end})).one()
    promoted_revenue = float(row.promoted_revenue)
    organic_revenue = float(row.organic_revenue)
    total_revenue = promoted_revenue + organic_revenue
    return {
        "period": period,
        "promoted_orders": int(row.promoted_orders),
        "organic_orders": int(row.organic_orders),
        "promoted_revenue": promoted_revenue,
        "organic_revenue": organic_revenue,
        "total_revenue": total_revenue,
        "promoted_share_pct": round(promoted_revenue / total_revenue * 100, 2)
        if total_revenue > 0
        else 0.0,
        "total_discount_given": float(row.total_discount_given),
        "campaign_discount_given": float(row.campaign_discount_given),
    }


async def get_discount_impact(period: str) -> dict:
    """Return revenue split (campaign-promoted vs organic) plus total discounts given."""
    async with get_session() as session:
        return await _get_discount_impact_impl(session, period)
