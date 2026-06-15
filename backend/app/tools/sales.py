from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas import MetricSnapshot

from .base import pct_delta, previous_window, resolve_period, tool_retry


async def _aggregate_sales(
    session: AsyncSession,
    start,
    end,
    region: str | None = None,
    sku: str | None = None,
) -> dict[str, float]:
    where = [
        "o.placed_at >= :start",
        "o.placed_at < :end",
        "o.status NOT IN ('cancelled')",
    ]
    params: dict[str, object] = {"start": start, "end": end}

    if region:
        where.append("o.region = :region")
        params["region"] = region
    if sku:
        where.append(
            "EXISTS (SELECT 1 FROM order_items oi2 WHERE oi2.order_id = o.id AND oi2.sku = :sku)"
        )
        params["sku"] = sku

    order_sql = text(
        """
        SELECT
            COUNT(*) AS order_count,
            COALESCE(SUM(o.total_amount), 0) AS revenue,
            COALESCE(AVG(o.total_amount), 0) AS aov
        FROM orders o
        WHERE """
        + " AND ".join(where)
    )
    order_row = (await session.execute(order_sql, params)).one()

    units_where = [
        "o.placed_at >= :start",
        "o.placed_at < :end",
        "o.status NOT IN ('cancelled')",
    ]
    if region:
        units_where.append("o.region = :region")
    if sku:
        units_where.append("oi.sku = :sku")

    units_sql = text(
        """
        SELECT COALESCE(SUM(oi.quantity), 0) AS units_sold
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        WHERE """
        + " AND ".join(units_where)
    )
    units_row = (await session.execute(units_sql, params)).one()

    return {
        "order_count": int(order_row.order_count or 0),
        "revenue": float(order_row.revenue or 0),
        "aov": float(order_row.aov or 0),
        "units_sold": int(units_row.units_sold or 0),
    }


@tool_retry
async def _analyze_sales_impl(
    session: AsyncSession,
    period: str,
    region: str | None = None,
    sku: str | None = None,
    compare_previous: bool = True,
) -> dict:
    start, end = resolve_period(period)
    current = await _aggregate_sales(session, start, end, region=region, sku=sku)

    previous: dict[str, float] = {}
    if compare_previous:
        prev_start, prev_end = previous_window(start, end)
        previous = await _aggregate_sales(session, prev_start, prev_end, region=region, sku=sku)

    return {
        "period": period,
        "region": region,
        "sku": sku,
        "revenue": current["revenue"],
        "order_count": current["order_count"],
        "average_order_value": current["aov"],
        "units_sold": current["units_sold"],
        "revenue_delta_pct": pct_delta(current["revenue"], previous.get("revenue", 0))
        if compare_previous
        else None,
        "order_count_delta_pct": pct_delta(current["order_count"], previous.get("order_count", 0))
        if compare_previous
        else None,
        "aov_delta_pct": pct_delta(current["aov"], previous.get("aov", 0))
        if compare_previous
        else None,
        "units_sold_delta_pct": pct_delta(current["units_sold"], previous.get("units_sold", 0))
        if compare_previous
        else None,
    }


async def analyze_sales(
    period: str,
    region: str | None = None,
    sku: str | None = None,
    compare_previous: bool = True,
) -> dict:
    async with get_session() as session:
        return await _analyze_sales_impl(
            session, period, region=region, sku=sku, compare_previous=compare_previous
        )


@tool_retry
async def _get_top_products_impl(
    session: AsyncSession,
    period: str,
    limit: int = 10,
) -> list[dict]:
    start, end = resolve_period(period)

    sql = text(
        """
        SELECT
            oi.sku,
            p.name,
            COALESCE(SUM(oi.quantity), 0) AS units_sold,
            COALESCE(SUM(oi.line_total), 0) AS revenue
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        JOIN products p ON p.sku = oi.sku
        WHERE o.placed_at >= :start
          AND o.placed_at < :end
          AND o.status NOT IN ('cancelled')
        GROUP BY oi.sku, p.name
        ORDER BY units_sold DESC
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


async def get_top_products(period: str, limit: int = 10) -> list[dict]:
    async with get_session() as session:
        return await _get_top_products_impl(session, period, limit=limit)


@tool_retry
async def _get_declining_products_impl(
    session: AsyncSession,
    period: str,
    limit: int = 10,
) -> list[dict]:
    start, end = resolve_period(period)
    prev_start, prev_end = previous_window(start, end)

    sql = text(
        """
        WITH current_period AS (
            SELECT
                oi.sku,
                COALESCE(SUM(oi.quantity), 0)   AS units,
                COALESCE(SUM(oi.line_total), 0) AS revenue
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE o.placed_at >= :start
              AND o.placed_at < :end
              AND o.status NOT IN ('cancelled')
            GROUP BY oi.sku
        ),
        prev_period AS (
            SELECT
                oi.sku,
                COALESCE(SUM(oi.quantity), 0)   AS units,
                COALESCE(SUM(oi.line_total), 0) AS revenue
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE o.placed_at >= :prev_start
              AND o.placed_at < :prev_end
              AND o.status NOT IN ('cancelled')
            GROUP BY oi.sku
        )
        SELECT
            c.sku,
            p.name,
            c.revenue  AS current_revenue,
            pr.revenue AS prev_revenue,
            c.units    AS current_units,
            pr.units   AS prev_units
        FROM current_period c
        JOIN products p  ON p.sku  = c.sku
        JOIN prev_period pr ON pr.sku = c.sku
        WHERE c.revenue < pr.revenue
           OR c.units   < pr.units
        ORDER BY (c.revenue - pr.revenue) ASC
        LIMIT :limit
        """
    )
    rows = (
        await session.execute(
            sql,
            {
                "start": start,
                "end": end,
                "prev_start": prev_start,
                "prev_end": prev_end,
                "limit": limit,
            },
        )
    ).all()

    return [
        {
            "sku": row.sku,
            "name": row.name,
            "revenue_change_pct": pct_delta(float(row.current_revenue), float(row.prev_revenue)),
            "units_change_pct": pct_delta(float(row.current_units), float(row.prev_units)),
        }
        for row in rows
    ]


async def get_declining_products(period: str, limit: int = 10) -> list[dict]:
    async with get_session() as session:
        return await _get_declining_products_impl(session, period, limit=limit)


_ALLOWED_GROUP_BY = frozenset({"region", "channel"})


@tool_retry
async def _get_sales_distribution_impl(
    session: AsyncSession,
    period: str,
    group_by: str = "region",
) -> list[dict]:
    if group_by not in _ALLOWED_GROUP_BY:
        raise ValueError(f"group_by must be one of {sorted(_ALLOWED_GROUP_BY)}, got: {group_by!r}")

    start, end = resolve_period(period)

    # group_by is validated against a strict allowlist above — safe to interpolate
    sql = text(
        f"""
        SELECT
            o.{group_by}                         AS group_key,
            COUNT(*)                              AS order_count,
            COALESCE(SUM(o.total_amount), 0)      AS revenue
        FROM orders o
        WHERE o.placed_at >= :start
          AND o.placed_at < :end
          AND o.status NOT IN ('cancelled')
        GROUP BY o.{group_by}
        ORDER BY revenue DESC
        """
    )
    rows = (await session.execute(sql, {"start": start, "end": end})).all()

    return [
        {
            group_by: row.group_key,
            "order_count": int(row.order_count),
            "revenue": float(row.revenue),
        }
        for row in rows
    ]


async def get_sales_distribution(period: str, group_by: str = "region") -> list[dict]:
    async with get_session() as session:
        return await _get_sales_distribution_impl(session, period, group_by=group_by)


async def get_sales_metrics(period: str) -> list[MetricSnapshot]:
    """Return key sales figures as MetricSnapshot objects for the given period."""
    data = await analyze_sales(period, compare_previous=True)
    return [
        MetricSnapshot(
            name="revenue",
            value=data["revenue"],
            unit="usd",
            period=period,
            delta_pct=data.get("revenue_delta_pct"),
        ),
        MetricSnapshot(
            name="order_count",
            value=data["order_count"],
            unit="orders",
            period=period,
            delta_pct=data.get("order_count_delta_pct"),
        ),
        MetricSnapshot(
            name="average_order_value",
            value=data["average_order_value"],
            unit="usd",
            period=period,
            delta_pct=data.get("aov_delta_pct"),
        ),
        MetricSnapshot(
            name="units_sold",
            value=data["units_sold"],
            unit="units",
            period=period,
            delta_pct=data.get("units_sold_delta_pct"),
        ),
    ]


@tool_retry
async def _get_sales_by_sku_impl(session: AsyncSession, sku: str, period: str) -> dict:
    start, end = resolve_period(period)
    prev_start, prev_end = previous_window(start, end)
    sql = text(
        """
        SELECT COALESCE(SUM(oi.quantity), 0)       AS units_sold,
               COALESCE(SUM(oi.line_total), 0)     AS revenue,
               COUNT(DISTINCT oi.order_id)         AS order_count
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        WHERE oi.sku = :sku
          AND o.placed_at >= :start
          AND o.placed_at < :end
          AND o.status NOT IN ('cancelled')
        """
    )
    row = (await session.execute(sql, {"sku": sku, "start": start, "end": end})).one()
    prev = (await session.execute(sql, {"sku": sku, "start": prev_start, "end": prev_end})).one()
    current_revenue = float(row.revenue)
    prev_revenue = float(prev.revenue)
    current_units = int(row.units_sold)
    prev_units = int(prev.units_sold)
    return {
        "sku": sku,
        "period": period,
        "units_sold": current_units,
        "revenue": current_revenue,
        "order_count": int(row.order_count),
        "revenue_delta_pct": pct_delta(current_revenue, prev_revenue),
        "units_delta_pct": pct_delta(float(current_units), float(prev_units)),
        "prev_units_sold": prev_units,
        "prev_revenue": prev_revenue,
    }


async def get_sales_by_sku(sku: str, period: str) -> dict:
    """Return units sold, revenue, and period-over-period deltas for a single SKU."""
    async with get_session() as session:
        return await _get_sales_by_sku_impl(session, sku, period)


@tool_retry
async def _get_customer_segment_breakdown_impl(session: AsyncSession, period: str) -> list[dict]:
    start, end = resolve_period(period)
    sql = text(
        """
        SELECT COALESCE(c.customer_segment, 'unknown') AS segment,
               COUNT(DISTINCT o.id)                    AS order_count,
               COALESCE(SUM(o.total_amount), 0)        AS revenue,
               ROUND(AVG(o.total_amount), 2)           AS avg_order_value
        FROM orders o
        LEFT JOIN customers c ON c.id = o.customer_id
        WHERE o.placed_at >= :start
          AND o.placed_at < :end
          AND o.status NOT IN ('cancelled')
        GROUP BY COALESCE(c.customer_segment, 'unknown')
        ORDER BY revenue DESC
        """
    )
    rows = (await session.execute(sql, {"start": start, "end": end})).all()
    return [
        {
            "segment": row.segment,
            "order_count": int(row.order_count),
            "revenue": float(row.revenue),
            "avg_order_value": float(row.avg_order_value)
            if row.avg_order_value is not None
            else 0.0,
        }
        for row in rows
    ]


async def get_customer_segment_breakdown(period: str) -> list[dict]:
    """Break down orders and revenue by customer segment — is a drop from premium or economy?"""
    async with get_session() as session:
        return await _get_customer_segment_breakdown_impl(session, period)


@tool_retry
async def _get_orders_by_status_impl(session: AsyncSession, period: str) -> list[dict]:
    start, end = resolve_period(period)
    sql = text(
        """
        SELECT status,
               COUNT(*) AS order_count,
               COALESCE(SUM(total_amount), 0) AS total_value
        FROM orders
        WHERE placed_at >= :start
          AND placed_at < :end
        GROUP BY status
        ORDER BY order_count DESC
        """
    )
    rows = (await session.execute(sql, {"start": start, "end": end})).all()
    return [
        {
            "status": row.status,
            "order_count": int(row.order_count),
            "total_value": float(row.total_value),
        }
        for row in rows
    ]


async def get_orders_by_status(period: str) -> list[dict]:
    """Return order count and value grouped by status — high cancellation rate is a demand signal."""
    async with get_session() as session:
        return await _get_orders_by_status_impl(session, period)


@tool_retry
async def _get_campaign_revenue_attribution_impl(session: AsyncSession, period: str) -> dict:
    start, end = resolve_period(period)
    sql = text(
        """
        SELECT
            COUNT(*) FILTER (WHERE campaign_id IS NOT NULL)                          AS promoted_orders,
            COUNT(*) FILTER (WHERE campaign_id IS NULL)                              AS organic_orders,
            COALESCE(SUM(total_amount) FILTER (WHERE campaign_id IS NOT NULL), 0)   AS promoted_revenue,
            COALESCE(SUM(total_amount) FILTER (WHERE campaign_id IS NULL), 0)       AS organic_revenue,
            COALESCE(SUM(total_amount), 0)                                          AS total_revenue
        FROM orders
        WHERE placed_at >= :start
          AND placed_at < :end
          AND status NOT IN ('cancelled')
        """
    )
    row = (await session.execute(sql, {"start": start, "end": end})).one()
    total = float(row.total_revenue)
    promoted = float(row.promoted_revenue)
    return {
        "period": period,
        "total_revenue": total,
        "promoted_revenue": promoted,
        "organic_revenue": float(row.organic_revenue),
        "promoted_orders": int(row.promoted_orders),
        "organic_orders": int(row.organic_orders),
        "promoted_share_pct": round(promoted / total * 100, 2) if total > 0 else 0.0,
    }


async def get_campaign_revenue_attribution(period: str) -> dict:
    """Return revenue split between campaign-promoted and organic orders."""
    async with get_session() as session:
        return await _get_campaign_revenue_attribution_impl(session, period)


@tool_retry
async def _get_hourly_sales_trend_impl(session: AsyncSession, date: str) -> list[dict]:
    sql = text(
        """
        SELECT EXTRACT(HOUR FROM placed_at AT TIME ZONE 'UTC')::int AS hour,
               COUNT(*) AS order_count,
               COALESCE(SUM(total_amount), 0) AS revenue
        FROM orders
        WHERE placed_at::date = :date::date
          AND status NOT IN ('cancelled')
        GROUP BY EXTRACT(HOUR FROM placed_at AT TIME ZONE 'UTC')::int
        ORDER BY hour
        """
    )
    rows = (await session.execute(sql, {"date": date})).all()
    return [
        {
            "hour": row.hour,
            "order_count": int(row.order_count),
            "revenue": float(row.revenue),
        }
        for row in rows
    ]


async def get_hourly_sales_trend(date: str) -> list[dict]:
    """Return order count and revenue per UTC hour for a date (ISO: YYYY-MM-DD) — pinpoints when a drop started."""
    async with get_session() as session:
        return await _get_hourly_sales_trend_impl(session, date)
