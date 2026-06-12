from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

from .base import resolve_period, tool_retry


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
async def _get_low_stock_products_impl(session: AsyncSession) -> list[dict]:
    sql = text(
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
    rows = (await session.execute(sql)).all()

    return [
        {
            "sku": row.sku,
            "name": row.name,
            "quantity_on_hand": int(row.quantity_on_hand),
            "reorder_point": int(row.reorder_point),
            "reorder_quantity": int(row.reorder_quantity),
        }
        for row in rows
    ]


async def get_low_stock_products() -> list[dict]:
    async with get_session() as session:
        return await _get_low_stock_products_impl(session)


@tool_retry
async def _was_out_of_stock_impl(session: AsyncSession, sku: str, timestamp: datetime) -> bool:
    sql = text(
        """
        SELECT quantity_after
        FROM inventory_movements
        WHERE sku = :sku
          AND occurred_at <= :ts
        ORDER BY occurred_at DESC
        LIMIT 1
        """
    )
    row = (await session.execute(sql, {"sku": sku, "ts": timestamp})).one_or_none()
    if row is not None:
        return int(row.quantity_after) <= 0

    inv_sql = text("SELECT quantity_on_hand FROM inventory WHERE sku = :sku")
    inv = (await session.execute(inv_sql, {"sku": sku})).one_or_none()
    return inv is not None and int(inv.quantity_on_hand) <= 0


async def was_out_of_stock(sku: str, timestamp: datetime) -> bool:
    async with get_session() as session:
        return await _was_out_of_stock_impl(session, sku, timestamp)


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
