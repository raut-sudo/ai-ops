from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

from .base import previous_window, resolve_period, tool_retry


@tool_retry
async def _get_stock_level_impl(session: AsyncSession, sku: str) -> dict:
    sql = text(
        """
        SELECT
            i.sku,
            p.name,
            i.quantity_on_hand,
            i.reorder_point,
            i.quantity_reserved,
            i.last_restocked_at
        FROM inventory i
        JOIN products p ON p.sku = i.sku
        WHERE i.sku = :sku
        """
    )
    row = (await session.execute(sql, {"sku": sku})).one_or_none()
    if row is None:
        return {}

    return {
        "sku": row.sku,
        "name": row.name,
        "quantity_on_hand": int(row.quantity_on_hand),
        "reorder_point": int(row.reorder_point),
        "quantity_reserved": int(row.quantity_reserved),
        "is_low_stock": int(row.quantity_on_hand) <= int(row.reorder_point),
        "last_restocked_at": row.last_restocked_at,
    }


async def get_stock_level(sku: str) -> dict:
    async with get_session() as session:
        return await _get_stock_level_impl(session, sku)


@tool_retry
async def _analyze_inventory_impl(session: AsyncSession) -> dict:
    stats_sql = text(
        """
        SELECT
            COUNT(*) FILTER (WHERE i.quantity_on_hand <= i.reorder_point) AS low_stock_count,
            COUNT(*) FILTER (WHERE i.quantity_on_hand = 0)                AS stockout_count,
            COALESCE(SUM(i.quantity_on_hand * p.unit_price), 0)           AS inventory_value
        FROM inventory i
        JOIN products p ON p.sku = i.sku
        WHERE p.is_active = TRUE
        """
    )
    stats = (await session.execute(stats_sql)).one()

    low_sql = text(
        """
        SELECT
            i.sku,
            p.name,
            i.quantity_on_hand,
            i.reorder_point,
            i.reorder_quantity
        FROM inventory i
        JOIN products p ON p.sku = i.sku
        WHERE i.quantity_on_hand <= i.reorder_point
        ORDER BY i.quantity_on_hand ASC, i.reorder_point DESC
        """
    )
    rows = (await session.execute(low_sql)).all()

    return {
        "inventory_value": float(stats.inventory_value),
        "low_stock_count": int(stats.low_stock_count),
        "stockout_count": int(stats.stockout_count),
        "low_stock_products": [
            {
                "sku": row.sku,
                "name": row.name,
                "quantity_on_hand": int(row.quantity_on_hand),
                "reorder_point": int(row.reorder_point),
                "reorder_quantity": int(row.reorder_quantity),
            }
            for row in rows
        ],
    }


async def analyze_inventory() -> dict:
    async with get_session() as session:
        return await _analyze_inventory_impl(session)


@tool_retry
async def _get_inventory_turnover_impl(session: AsyncSession, period: str) -> list[dict]:
    start, end = resolve_period(period)

    sql = text(
        """
        SELECT
            i.sku,
            p.name,
            i.quantity_on_hand,
            COALESCE(SUM(oi.quantity), 0) AS units_sold
        FROM inventory i
        JOIN products p ON p.sku = i.sku
        LEFT JOIN order_items oi ON oi.sku = i.sku
        LEFT JOIN orders o
            ON  o.id = oi.order_id
            AND o.placed_at >= :start
            AND o.placed_at < :end
            AND o.status NOT IN ('cancelled')
        GROUP BY i.sku, p.name, i.quantity_on_hand
        ORDER BY units_sold DESC
        """
    )
    rows = (await session.execute(sql, {"start": start, "end": end})).all()

    return [
        {
            "sku": row.sku,
            "name": row.name,
            "units_sold": int(row.units_sold),
            "quantity_on_hand": int(row.quantity_on_hand),
            "turnover_ratio": round(int(row.units_sold) / int(row.quantity_on_hand), 4)
            if int(row.quantity_on_hand) > 0
            else None,
        }
        for row in rows
    ]


async def get_inventory_turnover(period: str) -> list[dict]:
    async with get_session() as session:
        return await _get_inventory_turnover_impl(session, period)


@tool_retry
async def _get_revenue_lost_to_stockouts_impl(session: AsyncSession, period: str) -> list[dict]:
    start, end = resolve_period(period)
    prev_start, _ = previous_window(start, end)

    sql = text(
        """
        WITH stockout_skus AS (
            SELECT DISTINCT sku
            FROM inventory_movements
            WHERE occurred_at >= :start
              AND occurred_at < :end
              AND quantity_after <= 0
        ),
        avg_daily_revenue AS (
            SELECT
                oi.sku,
                COALESCE(SUM(oi.line_total), 0)
                    / GREATEST(
                        EXTRACT(DAY FROM ((:start)::timestamptz - (:prev_start)::timestamptz))::int,
                        1
                    ) AS avg_daily_revenue
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE o.placed_at >= :prev_start
              AND o.placed_at <  :start
              AND o.status NOT IN ('cancelled')
            GROUP BY oi.sku
        ),
        stockout_durations AS (
            SELECT sku, COUNT(*) AS stockout_event_count
            FROM inventory_movements
            WHERE occurred_at >= :start
              AND occurred_at < :end
              AND quantity_after <= 0
            GROUP BY sku
        )
        SELECT
            ss.sku,
            p.name,
            COALESCE(adr.avg_daily_revenue, 0)                           AS avg_daily_revenue,
            sd.stockout_event_count,
            COALESCE(adr.avg_daily_revenue, 0) * sd.stockout_event_count AS estimated_revenue_lost
        FROM stockout_skus ss
        JOIN     products            p   ON  p.sku  = ss.sku
        LEFT JOIN avg_daily_revenue  adr ON  adr.sku = ss.sku
        LEFT JOIN stockout_durations sd  ON  sd.sku  = ss.sku
        ORDER BY estimated_revenue_lost DESC
        """
    )
    rows = (
        await session.execute(
            sql,
            {"start": start, "end": end, "prev_start": prev_start},
        )
    ).all()

    return [
        {
            "sku": row.sku,
            "name": row.name,
            "avg_daily_revenue": float(row.avg_daily_revenue),
            "stockout_event_count": int(row.stockout_event_count),
            "estimated_revenue_lost": float(row.estimated_revenue_lost),
        }
        for row in rows
    ]


async def get_revenue_lost_to_stockouts(period: str) -> list[dict]:
    async with get_session() as session:
        return await _get_revenue_lost_to_stockouts_impl(session, period)


@tool_retry
async def _get_stockout_history_impl(session: AsyncSession, sku: str, period: str) -> list[dict]:
    start, end = resolve_period(period)

    sql = text(
        """
        SELECT
            occurred_at,
            movement_type,
            quantity_change,
            quantity_after,
            reference_type,
            reference_id
        FROM inventory_movements
        WHERE sku = :sku
          AND occurred_at >= :start
          AND occurred_at < :end
          AND quantity_after <= 0
        ORDER BY occurred_at DESC
        """
    )
    rows = (await session.execute(sql, {"sku": sku, "start": start, "end": end})).all()

    return [
        {
            "occurred_at": row.occurred_at,
            "movement_type": row.movement_type,
            "quantity_change": int(row.quantity_change),
            "quantity_after": int(row.quantity_after),
            "reference_type": row.reference_type,
            "reference_id": row.reference_id,
        }
        for row in rows
    ]


async def get_stockout_history(sku: str, period: str) -> list[dict]:
    async with get_session() as session:
        return await _get_stockout_history_impl(session, sku, period)


async def get_low_stock_products() -> list[dict]:
    """Return products at or below their reorder point."""
    result = await analyze_inventory()
    return result["low_stock_products"]


async def was_out_of_stock(sku: str, ts: datetime) -> bool:
    """Return True if the SKU had a zero-stock event within 12 h either side of ts."""
    window_start = ts - timedelta(hours=12)
    window_end = ts + timedelta(hours=12)
    async with get_session() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM inventory_movements
                    WHERE sku = :sku
                      AND quantity_after <= 0
                      AND occurred_at >= :start
                      AND occurred_at < :end
                    """
                ),
                {"sku": sku, "start": window_start, "end": window_end},
            )
        ).one()
    return int(row.cnt) > 0


@tool_retry
async def _get_restock_history_impl(session: AsyncSession, sku: str, period: str) -> list[dict]:
    start, end = resolve_period(period)
    sql = text(
        """
        SELECT movement_type, quantity_change, quantity_after,
               reference_type, reference_id, occurred_at
        FROM inventory_movements
        WHERE sku = :sku
          AND movement_type = 'restock'
          AND occurred_at >= :start
          AND occurred_at < :end
        ORDER BY occurred_at DESC
        """
    )
    rows = (await session.execute(sql, {"sku": sku, "start": start, "end": end})).all()
    return [
        {
            "quantity_change": int(row.quantity_change),
            "quantity_after": int(row.quantity_after),
            "reference_type": row.reference_type,
            "reference_id": row.reference_id,
            "occurred_at": row.occurred_at,
        }
        for row in rows
    ]


async def get_restock_history(sku: str, period: str) -> list[dict]:
    """Return all restock events for a SKU in the period — when, how much, and from where."""
    async with get_session() as session:
        return await _get_restock_history_impl(session, sku, period)


@tool_retry
async def _get_products_near_reorder_point_impl(
    session: AsyncSession, buffer_pct: float
) -> list[dict]:
    sql = text(
        """
        SELECT i.sku, p.name, p.category,
               i.quantity_on_hand, i.reorder_point, i.reorder_quantity,
               i.last_restocked_at,
               ROUND(
                   (i.quantity_on_hand::numeric / NULLIF(i.reorder_point, 0) - 1) * 100,
               1) AS pct_above_reorder
        FROM inventory i
        JOIN products p ON p.sku = i.sku
        WHERE i.quantity_on_hand > i.reorder_point
          AND i.quantity_on_hand <= i.reorder_point * (1 + :buffer / 100.0)
          AND p.is_active = TRUE
        ORDER BY pct_above_reorder ASC
        """
    )
    rows = (await session.execute(sql, {"buffer": buffer_pct})).all()
    return [
        {
            "sku": row.sku,
            "name": row.name,
            "category": row.category,
            "quantity_on_hand": int(row.quantity_on_hand),
            "reorder_point": int(row.reorder_point),
            "reorder_quantity": int(row.reorder_quantity),
            "last_restocked_at": row.last_restocked_at,
            "pct_above_reorder": float(row.pct_above_reorder)
            if row.pct_above_reorder is not None
            else None,
        }
        for row in rows
    ]


async def get_products_near_reorder_point(buffer_pct: float = 20.0) -> list[dict]:
    """Return products above reorder point but within buffer_pct% of it — proactive low-stock warning."""
    async with get_session() as session:
        return await _get_products_near_reorder_point_impl(session, buffer_pct)


@tool_retry
async def _get_slow_moving_products_impl(
    session: AsyncSession, period: str, turnover_threshold: float
) -> list[dict]:
    start, end = resolve_period(period)
    sql = text(
        """
        WITH sold AS (
            SELECT oi.sku, COALESCE(SUM(oi.quantity), 0) AS units_sold
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE o.placed_at >= :start
              AND o.placed_at < :end
              AND o.status NOT IN ('cancelled')
            GROUP BY oi.sku
        )
        SELECT i.sku, p.name, p.category,
               i.quantity_on_hand,
               COALESCE(s.units_sold, 0) AS units_sold,
               ROUND(
                   COALESCE(s.units_sold, 0)::numeric / NULLIF(i.quantity_on_hand, 0),
               4) AS turnover_ratio
        FROM inventory i
        JOIN products p ON p.sku = i.sku
        LEFT JOIN sold s ON s.sku = i.sku
        WHERE i.quantity_on_hand > 0
          AND p.is_active = TRUE
          AND COALESCE(s.units_sold, 0)::numeric / NULLIF(i.quantity_on_hand, 0) < :threshold
        ORDER BY turnover_ratio ASC NULLS FIRST
        """
    )
    rows = (
        await session.execute(sql, {"start": start, "end": end, "threshold": turnover_threshold})
    ).all()
    return [
        {
            "sku": row.sku,
            "name": row.name,
            "category": row.category,
            "quantity_on_hand": int(row.quantity_on_hand),
            "units_sold": int(row.units_sold),
            "turnover_ratio": float(row.turnover_ratio) if row.turnover_ratio is not None else 0.0,
        }
        for row in rows
    ]


async def get_slow_moving_products(period: str, turnover_threshold: float = 0.1) -> list[dict]:
    """Return in-stock products with low sales velocity — capital tied up in dead stock."""
    async with get_session() as session:
        return await _get_slow_moving_products_impl(session, period, turnover_threshold)


@tool_retry
async def _get_inventory_movements_impl(session: AsyncSession, sku: str, period: str) -> list[dict]:
    start, end = resolve_period(period)
    sql = text(
        """
        SELECT movement_type, quantity_change, quantity_after,
               reference_type, reference_id, occurred_at
        FROM inventory_movements
        WHERE sku = :sku
          AND occurred_at >= :start
          AND occurred_at < :end
        ORDER BY occurred_at DESC
        """
    )
    rows = (await session.execute(sql, {"sku": sku, "start": start, "end": end})).all()
    return [
        {
            "movement_type": row.movement_type,
            "quantity_change": int(row.quantity_change),
            "quantity_after": int(row.quantity_after),
            "reference_type": row.reference_type,
            "reference_id": row.reference_id,
            "occurred_at": row.occurred_at,
        }
        for row in rows
    ]


async def get_inventory_movements(sku: str, period: str) -> list[dict]:
    """Return every movement (sale, restock, adjustment) for a SKU in the period — full audit trail."""
    async with get_session() as session:
        return await _get_inventory_movements_impl(session, sku, period)


@tool_retry
async def _get_product_details_impl(session: AsyncSession, sku: str) -> dict | None:
    sql = text(
        """
        SELECT sku, name, category, subcategory, brand,
               unit_price, cost_price, is_active
        FROM products
        WHERE sku = :sku
        """
    )
    row = (await session.execute(sql, {"sku": sku})).one_or_none()
    if row is None:
        return None
    return {
        "sku": row.sku,
        "name": row.name,
        "category": row.category,
        "subcategory": row.subcategory,
        "brand": row.brand,
        "unit_price": float(row.unit_price),
        "cost_price": float(row.cost_price) if row.cost_price is not None else None,
        "is_active": row.is_active,
    }


async def get_product_details(sku: str) -> dict | None:
    """Return product metadata (name, category, brand, unit price, cost price) for a SKU."""
    async with get_session() as session:
        return await _get_product_details_impl(session, sku)
