from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

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
