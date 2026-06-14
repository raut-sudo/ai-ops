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
