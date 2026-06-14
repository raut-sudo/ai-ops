from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool = True


def tool_retry(func):
    """Apply bounded exponential retry for transient DB/query errors."""
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )(func)


# Normalize common LLM shorthand aliases to canonical period names
_PERIOD_ALIASES: dict[str, str] = {
    "1d": "yesterday",
    "7d": "last_7_days",
    "last_week": "last_7_days",
    "weekly": "last_7_days",
    "30d": "last_30_days",
    "last_month": "last_30_days",
    "monthly": "last_30_days",
}


def resolve_period(period: str) -> tuple[datetime, datetime]:
    """Map semantic time windows into UTC [start, end) timestamps."""
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    period = _PERIOD_ALIASES.get(period, period)

    if period == "yesterday":
        return end - timedelta(days=1), end
    if period == "last_7_days":
        return end - timedelta(days=7), end
    if period == "last_30_days":
        return end - timedelta(days=30), end

    raise ValueError(f"Unsupported period: {period}")


def previous_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Return immediately preceding [start, end) window with same duration."""
    window = end - start
    return start - window, start


def pct_delta(current: float, previous: float) -> float | None:
    """Compute percent delta, returning None when baseline is zero."""
    if previous == 0:
        return None
    return ((current - previous) / previous) * 100.0
