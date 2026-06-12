from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas import MetricSnapshot

from .base import resolve_period, tool_retry


@tool_retry
async def _get_support_metrics_impl(session: AsyncSession, period: str) -> list[MetricSnapshot]:
    start, end = resolve_period(period)

    sql = text(
        """
        SELECT
            COUNT(*) AS ticket_count,
            COALESCE(AVG(sentiment_score), 0) AS avg_sentiment,
            COUNT(*) FILTER (WHERE sentiment_score < 0) AS negative_ticket_count
        FROM support_tickets
        WHERE created_at >= :start
          AND created_at < :end
        """
    )
    row = (await session.execute(sql, {"start": start, "end": end})).one()

    return [
        MetricSnapshot(
            name="ticket_count",
            value=int(row.ticket_count),
            unit="tickets",
            period=period,
        ),
        MetricSnapshot(
            name="average_sentiment",
            value=float(row.avg_sentiment),
            unit="score",
            period=period,
        ),
        MetricSnapshot(
            name="negative_ticket_count",
            value=int(row.negative_ticket_count),
            unit="tickets",
            period=period,
        ),
    ]


async def get_support_metrics(period: str) -> list[MetricSnapshot]:
    async with get_session() as session:
        return await _get_support_metrics_impl(session, period)


@tool_retry
async def _get_complaints_by_sku_impl(
    session: AsyncSession,
    sku: str,
    period: str,
) -> list[dict]:
    start, end = resolve_period(period)

    sql = text(
        """
        SELECT
            ticket_number,
            category,
            priority,
            status,
            sentiment_score,
            subject,
            created_at
        FROM support_tickets
        WHERE related_sku = :sku
          AND created_at >= :start
          AND created_at < :end
          AND sentiment_score < 0
        ORDER BY created_at DESC
        """
    )
    rows = (await session.execute(sql, {"sku": sku, "start": start, "end": end})).all()

    return [
        {
            "ticket_number": row.ticket_number,
            "category": row.category,
            "priority": row.priority,
            "status": row.status,
            "sentiment_score": float(row.sentiment_score),
            "subject": row.subject,
            "created_at": row.created_at,
        }
        for row in rows
    ]


async def get_complaints_by_sku(sku: str, period: str) -> list[dict]:
    async with get_session() as session:
        return await _get_complaints_by_sku_impl(session, sku, period)


@tool_retry
async def _get_refund_rate_impl(session: AsyncSession, period: str) -> MetricSnapshot:
    start, end = resolve_period(period)

    returns_sql = text(
        """
        SELECT COUNT(*) AS return_count
        FROM returns
        WHERE requested_at >= :start
          AND requested_at < :end
        """
    )
    orders_sql = text(
        """
        SELECT COUNT(*) AS order_count
        FROM orders
        WHERE placed_at >= :start
          AND placed_at < :end
          AND status NOT IN ('cancelled')
        """
    )

    return_count = int(
        (await session.execute(returns_sql, {"start": start, "end": end})).one().return_count
    )
    order_count = int(
        (await session.execute(orders_sql, {"start": start, "end": end})).one().order_count
    )
    rate = (return_count / order_count) * 100.0 if order_count > 0 else 0.0

    return MetricSnapshot(
        name="refund_rate",
        value=rate,
        unit="percent",
        period=period,
    )


async def get_refund_rate(period: str) -> MetricSnapshot:
    async with get_session() as session:
        return await _get_refund_rate_impl(session, period)


@tool_retry
async def _get_ticket_trends_impl(session: AsyncSession, period: str) -> list[dict]:
    start, end = resolve_period(period)

    sql = text(
        """
        SELECT
            category,
            COUNT(*) AS ticket_count,
            COUNT(*) FILTER (WHERE sentiment_score < 0) AS negative_count
        FROM support_tickets
        WHERE created_at >= :start
          AND created_at < :end
        GROUP BY category
        ORDER BY ticket_count DESC
        """
    )
    rows = (await session.execute(sql, {"start": start, "end": end})).all()

    return [
        {
            "category": row.category,
            "ticket_count": int(row.ticket_count),
            "negative_count": int(row.negative_count),
            "negative_ratio_pct": (int(row.negative_count) / int(row.ticket_count) * 100.0)
            if int(row.ticket_count) > 0
            else 0.0,
        }
        for row in rows
    ]


async def get_ticket_trends(period: str) -> list[dict]:
    async with get_session() as session:
        return await _get_ticket_trends_impl(session, period)
