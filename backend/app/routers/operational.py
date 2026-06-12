"""GET /operational/stock/{sku} — live inventory for before/after demo panel (§19.1, R3.3)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import get_session

router = APIRouter(tags=["operational"])


class StockLevel(BaseModel):
    sku: str
    quantity_on_hand: int
    quantity_reserved: int
    reorder_point: int
    reorder_quantity: int
    warehouse_id: str
    updated_at: datetime


@router.get("/operational/stock/{sku}", response_model=StockLevel)
async def get_stock(sku: str) -> StockLevel:
    """Return current inventory snapshot for a SKU (Layer 1 read)."""
    async with get_session() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT sku, quantity_on_hand, quantity_reserved,
                           reorder_point, reorder_quantity, warehouse_id, updated_at
                    FROM inventory
                    WHERE sku = :sku
                    """
                ),
                {"sku": sku},
            )
        ).fetchone()

    if row is None:
        raise HTTPException(404, f"SKU {sku!r} not found in inventory.")

    return StockLevel(
        sku=row.sku,
        quantity_on_hand=row.quantity_on_hand,
        quantity_reserved=row.quantity_reserved,
        reorder_point=row.reorder_point,
        reorder_quantity=row.reorder_quantity,
        warehouse_id=row.warehouse_id,
        updated_at=row.updated_at,
    )
