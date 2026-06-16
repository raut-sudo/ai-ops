from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas import MetricSnapshot

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


async def get_support_metrics(period: str) -> list[MetricSnapshot]:
    """Return key support figures as MetricSnapshot objects."""
    data = await analyze_support(period)
    return [
        MetricSnapshot(
            name="ticket_count", value=data["ticket_count"], unit="tickets", period=period
        ),
        MetricSnapshot(
            name="negative_ticket_count",
            value=data["negative_ticket_count"],
            unit="tickets",
            period=period,
        ),
        MetricSnapshot(
            name="average_sentiment", value=data["average_sentiment"], unit="score", period=period
        ),
        MetricSnapshot(
            name="refund_rate", value=data["refund_rate_pct"], unit="pct", period=period
        ),
    ]


async def get_refund_rate(period: str) -> MetricSnapshot:
    """Return refund rate as a MetricSnapshot."""
    data = await analyze_support(period)
    return MetricSnapshot(
        name="refund_rate", value=data["refund_rate_pct"], unit="pct", period=period
    )


async def get_ticket_trends(period: str) -> list[dict]:
    """Return ticket counts grouped by category (alias for get_common_complaint_categories)."""
    return await get_common_complaint_categories(period)


async def get_complaints_by_sku(sku: str, period: str) -> list[dict]:
    """Return support tickets with negative sentiment for the given SKU in the period."""
    start, end = resolve_period(period)
    async with get_session() as session:
        sql = text(
            """
            SELECT id, ticket_number, category, priority, status,
                   subject, sentiment_score, created_at
            FROM support_tickets
            WHERE related_sku = :sku
              AND sentiment_score < 0
              AND created_at >= :start
              AND created_at < :end
            ORDER BY sentiment_score ASC
            """
        )
        rows = (await session.execute(sql, {"sku": sku, "start": start, "end": end})).all()

    return [
        {
            "ticket_number": row.ticket_number,
            "category": row.category,
            "priority": row.priority,
            "status": row.status,
            "subject": row.subject,
            "sentiment_score": float(row.sentiment_score),
            "created_at": row.created_at,
        }
        for row in rows
    ]


@tool_retry
async def _get_ticket_resolution_times_impl(session: AsyncSession, period: str) -> dict:
    start, end = resolve_period(period)
    sql = text(
        """
        SELECT
            priority,
            COUNT(*) AS resolved_count,
            ROUND(
                AVG(EXTRACT(EPOCH FROM (resolved_at - created_at)) / 3600),
            2) AS avg_hours,
            ROUND(
                PERCENTILE_CONT(0.95) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (resolved_at - created_at)) / 3600
                )::numeric,
            2) AS p95_hours
        FROM support_tickets
        WHERE created_at >= :start
          AND created_at < :end
          AND resolved_at IS NOT NULL
        GROUP BY priority
        ORDER BY avg_hours DESC
        """
    )
    rows = (await session.execute(sql, {"start": start, "end": end})).all()
    return {
        "period": period,
        "by_priority": [
            {
                "priority": row.priority,
                "resolved_count": int(row.resolved_count),
                "avg_hours": float(row.avg_hours) if row.avg_hours is not None else None,
                "p95_hours": float(row.p95_hours) if row.p95_hours is not None else None,
            }
            for row in rows
        ],
    }


async def get_ticket_resolution_times(period: str) -> dict:
    """Return average and p95 ticket resolution time in hours, grouped by priority — SLA signal."""
    async with get_session() as session:
        return await _get_ticket_resolution_times_impl(session, period)


@tool_retry
async def _get_open_tickets_snapshot_impl(session: AsyncSession) -> list[dict]:
    sql = text(
        """
        SELECT priority, category,
               COUNT(*) AS ticket_count,
               MIN(created_at) AS oldest_ticket_at
        FROM support_tickets
        WHERE status NOT IN ('resolved', 'closed')
        GROUP BY priority, category
        ORDER BY
            CASE priority
                WHEN 'critical' THEN 1
                WHEN 'high'     THEN 2
                WHEN 'medium'   THEN 3
                ELSE 4
            END,
            ticket_count DESC
        """
    )
    rows = (await session.execute(sql)).all()
    return [
        {
            "priority": row.priority,
            "category": row.category,
            "ticket_count": int(row.ticket_count),
            "oldest_ticket_at": row.oldest_ticket_at,
        }
        for row in rows
    ]


async def get_open_tickets_snapshot() -> list[dict]:
    """Return current open ticket backlog grouped by priority and category — live queue state."""
    async with get_session() as session:
        return await _get_open_tickets_snapshot_impl(session)


@tool_retry
async def _get_sentiment_trend_impl(session: AsyncSession, period: str) -> list[dict]:
    start, end = resolve_period(period)
    sql = text(
        """
        SELECT DATE_TRUNC('day', created_at)::date AS day,
               COUNT(*) AS ticket_count,
               ROUND(AVG(sentiment_score), 3) AS avg_sentiment,
               COUNT(*) FILTER (WHERE sentiment_score < 0) AS negative_count
        FROM support_tickets
        WHERE created_at >= :start
          AND created_at < :end
          AND sentiment_score IS NOT NULL
        GROUP BY DATE_TRUNC('day', created_at)::date
        ORDER BY day
        """
    )
    rows = (await session.execute(sql, {"start": start, "end": end})).all()
    return [
        {
            "day": str(row.day),
            "ticket_count": int(row.ticket_count),
            "avg_sentiment": float(row.avg_sentiment),
            "negative_count": int(row.negative_count),
        }
        for row in rows
    ]


async def get_sentiment_trend(period: str) -> list[dict]:
    """Return daily average sentiment score over the period — is customer mood worsening?"""
    async with get_session() as session:
        return await _get_sentiment_trend_impl(session, period)


@tool_retry
async def _get_return_rate_by_sku_impl(session: AsyncSession, sku: str, period: str) -> dict:
    start, end = resolve_period(period)
    params = {"sku": sku, "start": start, "end": end}

    units_sql = text(
        """
        SELECT COALESCE(SUM(oi.quantity), 0) AS units_sold
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        WHERE oi.sku = :sku
          AND o.placed_at >= :start
          AND o.placed_at < :end
          AND o.status NOT IN ('cancelled')
        """
    )
    returns_sql = text(
        """
        SELECT COALESCE(SUM(quantity), 0)      AS units_returned,
               COALESCE(SUM(refund_amount), 0) AS total_refund_amount,
               COUNT(*) AS return_count
        FROM returns
        WHERE sku = :sku
          AND requested_at >= :start
          AND requested_at < :end
        """
    )
    reasons_sql = text(
        """
        SELECT reason, COUNT(*) AS cnt
        FROM returns
        WHERE sku = :sku
          AND requested_at >= :start
          AND requested_at < :end
        GROUP BY reason
        ORDER BY cnt DESC
        LIMIT 5
        """
    )
    units_row = (await session.execute(units_sql, params)).one()
    returns_row = (await session.execute(returns_sql, params)).one()
    reason_rows = (await session.execute(reasons_sql, params)).all()

    units_sold = int(units_row.units_sold)
    units_returned = int(returns_row.units_returned)
    return {
        "sku": sku,
        "period": period,
        "units_sold": units_sold,
        "units_returned": units_returned,
        "return_count": int(returns_row.return_count),
        "total_refund_amount": float(returns_row.total_refund_amount),
        "return_rate_pct": round(units_returned / units_sold * 100, 2) if units_sold > 0 else 0.0,
        "top_reasons": [{"reason": r.reason, "count": int(r.cnt)} for r in reason_rows],
    }


async def get_return_rate_by_sku(sku: str, period: str) -> dict:
    """Return rate, refund amount, and top return reasons for a specific SKU."""
    async with get_session() as session:
        return await _get_return_rate_by_sku_impl(session, sku, period)
