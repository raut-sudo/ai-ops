from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

from .base import resolve_period, tool_retry


@tool_retry
async def _analyze_support_impl(session: AsyncSession, period: str) -> dict:
    start, end = resolve_period(period)

    tickets_sql = text(
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
    ticket_row = (await session.execute(tickets_sql, {"start": start, "end": end})).one()

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
    refund_rate = (return_count / order_count) * 100.0 if order_count > 0 else 0.0

    return {
        "period": period,
        "ticket_count": int(ticket_row.ticket_count),
        "average_sentiment": float(ticket_row.avg_sentiment),
        "negative_ticket_count": int(ticket_row.negative_ticket_count),
        "return_count": return_count,
        "order_count": order_count,
        "refund_rate_pct": refund_rate,
    }


async def analyze_support(period: str) -> dict:
    async with get_session() as session:
        return await _analyze_support_impl(session, period)


@tool_retry
async def _get_products_with_high_complaint_rate_impl(
    session: AsyncSession,
    period: str,
    min_complaints: int = 1,
) -> list[dict]:
    start, end = resolve_period(period)

    sql = text(
        """
        SELECT
            st.related_sku                                              AS sku,
            p.name,
            COUNT(st.id)                                                AS complaint_count,
            COALESCE(SUM(oi.quantity), 0)                              AS units_sold,
            CASE
                WHEN COALESCE(SUM(oi.quantity), 0) > 0
                THEN ROUND(COUNT(st.id)::numeric / SUM(oi.quantity) * 100, 2)
                ELSE NULL
            END                                                         AS complaint_rate_pct
        FROM support_tickets st
        JOIN products p ON p.sku = st.related_sku
        LEFT JOIN order_items oi ON oi.sku = st.related_sku
        LEFT JOIN orders o
            ON  o.id = oi.order_id
            AND o.placed_at >= :start
            AND o.placed_at <  :end
            AND o.status NOT IN ('cancelled')
        WHERE st.related_sku IS NOT NULL
          AND st.created_at >= :start
          AND st.created_at <  :end
        GROUP BY st.related_sku, p.name
        HAVING COUNT(st.id) >= :min_complaints
        ORDER BY complaint_count DESC
        """
    )
    rows = (
        await session.execute(sql, {"start": start, "end": end, "min_complaints": min_complaints})
    ).all()

    return [
        {
            "sku": row.sku,
            "name": row.name,
            "complaint_count": int(row.complaint_count),
            "units_sold": int(row.units_sold),
            "complaint_rate_pct": float(row.complaint_rate_pct)
            if row.complaint_rate_pct is not None
            else None,
        }
        for row in rows
    ]


async def get_products_with_high_complaint_rate(period: str, min_complaints: int = 1) -> list[dict]:
    async with get_session() as session:
        return await _get_products_with_high_complaint_rate_impl(
            session, period, min_complaints=min_complaints
        )


@tool_retry
async def _get_common_complaint_categories_impl(session: AsyncSession, period: str) -> list[dict]:
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


async def get_common_complaint_categories(period: str) -> list[dict]:
    async with get_session() as session:
        return await _get_common_complaint_categories_impl(session, period)


@tool_retry
async def _get_common_return_reasons_impl(session: AsyncSession, period: str) -> list[dict]:
    start, end = resolve_period(period)

    sql = text(
        """
        SELECT
            reason,
            COUNT(*) AS return_count,
            COALESCE(SUM(refund_amount), 0) AS total_refund_amount
        FROM returns
        WHERE requested_at >= :start
          AND requested_at < :end
        GROUP BY reason
        ORDER BY return_count DESC
        """
    )
    rows = (await session.execute(sql, {"start": start, "end": end})).all()

    return [
        {
            "reason": row.reason,
            "return_count": int(row.return_count),
            "total_refund_amount": float(row.total_refund_amount),
        }
        for row in rows
    ]


async def get_common_return_reasons(period: str) -> list[dict]:
    async with get_session() as session:
        return await _get_common_return_reasons_impl(session, period)


@tool_retry
async def _get_churn_risk_products_impl(
    session: AsyncSession,
    period: str,
    sentiment_threshold: float = -0.2,
    min_returns: int = 1,
) -> list[dict]:
    start, end = resolve_period(period)

    sql = text(
        """
        WITH ticket_stats AS (
            SELECT
                related_sku          AS sku,
                COUNT(*)             AS complaint_count,
                AVG(sentiment_score) AS avg_sentiment
            FROM support_tickets
            WHERE related_sku IS NOT NULL
              AND created_at >= :start
              AND created_at <  :end
            GROUP BY related_sku
        ),
        return_stats AS (
            SELECT
                sku,
                COUNT(*) AS return_count
            FROM returns
            WHERE requested_at >= :start
              AND requested_at <  :end
            GROUP BY sku
        )
        SELECT
            COALESCE(ts.sku, rs.sku)        AS sku,
            p.name,
            COALESCE(ts.complaint_count, 0) AS complaint_count,
            COALESCE(ts.avg_sentiment, 0)   AS avg_sentiment,
            COALESCE(rs.return_count, 0)    AS return_count
        FROM ticket_stats ts
        FULL OUTER JOIN return_stats rs ON rs.sku = ts.sku
        JOIN products p ON p.sku = COALESCE(ts.sku, rs.sku)
        WHERE COALESCE(ts.avg_sentiment, 0) < :sentiment_threshold
           OR COALESCE(rs.return_count, 0)  >= :min_returns
        ORDER BY avg_sentiment ASC, return_count DESC
        """
    )
    rows = (
        await session.execute(
            sql,
            {
                "start": start,
                "end": end,
                "sentiment_threshold": sentiment_threshold,
                "min_returns": min_returns,
            },
        )
    ).all()

    return [
        {
            "sku": row.sku,
            "name": row.name,
            "complaint_count": int(row.complaint_count),
            "avg_sentiment": float(row.avg_sentiment),
            "return_count": int(row.return_count),
        }
        for row in rows
    ]


async def get_churn_risk_products(
    period: str,
    sentiment_threshold: float = -0.2,
    min_returns: int = 1,
) -> list[dict]:
    async with get_session() as session:
        return await _get_churn_risk_products_impl(
            session,
            period,
            sentiment_threshold=sentiment_threshold,
            min_returns=min_returns,
        )
