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
async def _get_sales_metrics_impl(
    session: AsyncSession,
    period: str,
    region: str | None = None,
    sku: str | None = None,
) -> list[MetricSnapshot]:
    start, end = resolve_period(period)
    prev_start, prev_end = previous_window(start, end)

    current = await _aggregate_sales(session, start, end, region=region, sku=sku)
    previous = await _aggregate_sales(session, prev_start, prev_end, region=region, sku=sku)

    return [
        MetricSnapshot(
            name="revenue",
            value=current["revenue"],
            unit="USD",
            period=period,
            delta_pct=pct_delta(current["revenue"], previous["revenue"]),
        ),
        MetricSnapshot(
            name="order_count",
            value=current["order_count"],
            unit="orders",
            period=period,
            delta_pct=pct_delta(current["order_count"], previous["order_count"]),
        ),
        MetricSnapshot(
            name="average_order_value",
            value=current["aov"],
            unit="USD",
            period=period,
            delta_pct=pct_delta(current["aov"], previous["aov"]),
        ),
        MetricSnapshot(
            name="units_sold",
            value=current["units_sold"],
            unit="units",
            period=period,
            delta_pct=pct_delta(current["units_sold"], previous["units_sold"]),
        ),
    ]


async def get_sales_metrics(
    period: str,
    region: str | None = None,
    sku: str | None = None,
) -> list[MetricSnapshot]:
    async with get_session() as session:
        return await _get_sales_metrics_impl(session, period, region=region, sku=sku)


@tool_retry
async def _compare_sales_periods_impl(
    session: AsyncSession,
    period_a: str,
    period_b: str,
) -> list[MetricSnapshot]:
    a_start, a_end = resolve_period(period_a)
    b_start, b_end = resolve_period(period_b)

    metrics_a = await _aggregate_sales(session, a_start, a_end)
    metrics_b = await _aggregate_sales(session, b_start, b_end)

    return [
        MetricSnapshot(
            name="revenue",
            value=metrics_a["revenue"],
            unit="USD",
            period=period_a,
            delta_pct=pct_delta(metrics_a["revenue"], metrics_b["revenue"]),
        ),
        MetricSnapshot(
            name="order_count",
            value=metrics_a["order_count"],
            unit="orders",
            period=period_a,
            delta_pct=pct_delta(metrics_a["order_count"], metrics_b["order_count"]),
        ),
        MetricSnapshot(
            name="average_order_value",
            value=metrics_a["aov"],
            unit="USD",
            period=period_a,
            delta_pct=pct_delta(metrics_a["aov"], metrics_b["aov"]),
        ),
        MetricSnapshot(
            name="units_sold",
            value=metrics_a["units_sold"],
            unit="units",
            period=period_a,
            delta_pct=pct_delta(metrics_a["units_sold"], metrics_b["units_sold"]),
        ),
    ]


async def compare_sales_periods(period_a: str, period_b: str) -> list[MetricSnapshot]:
    async with get_session() as session:
        return await _compare_sales_periods_impl(session, period_a, period_b)


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
async def _get_sales_by_region_impl(session: AsyncSession, period: str) -> list[dict]:
    start, end = resolve_period(period)

    sql = text(
        """
        SELECT
            o.region,
            COUNT(*) AS order_count,
            COALESCE(SUM(o.total_amount), 0) AS revenue
        FROM orders o
        WHERE o.placed_at >= :start
          AND o.placed_at < :end
          AND o.status NOT IN ('cancelled')
        GROUP BY o.region
        ORDER BY revenue DESC
        """
    )
    rows = (await session.execute(sql, {"start": start, "end": end})).all()

    return [
        {
            "region": row.region,
            "order_count": int(row.order_count),
            "revenue": float(row.revenue),
        }
        for row in rows
    ]


async def get_sales_by_region(period: str) -> list[dict]:
    async with get_session() as session:
        return await _get_sales_by_region_impl(session, period)
