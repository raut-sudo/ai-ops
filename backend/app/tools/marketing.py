from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

from .base import resolve_period, tool_retry


def _derive_rates(impressions: int, clicks: int, spend: float, attributed_revenue: float) -> dict:
    ctr = (clicks / impressions * 100.0) if impressions > 0 else 0.0
    roas = (attributed_revenue / spend) if spend > 0 else 0.0
    conversion_rate = (attributed_revenue / clicks) if clicks > 0 else 0.0
    return {
        "ctr_pct": ctr,
        "roas": roas,
        "revenue_per_click": conversion_rate,
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


async def get_campaign_performance(
    period: str,
    campaign_id: str | None = None,
) -> list[dict]:
    async with get_session() as session:
        return await _get_campaign_performance_impl(session, period, campaign_id=campaign_id)


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
async def _get_active_campaigns_for_sku_impl(session: AsyncSession, sku: str) -> list[dict]:
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


async def get_active_campaigns_for_sku(sku: str) -> list[dict]:
    async with get_session() as session:
        return await _get_active_campaigns_for_sku_impl(session, sku)
