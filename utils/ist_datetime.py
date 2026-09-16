"""
India Standard Time (Asia/Kolkata) helpers.

Workflow timestamps in this app are commonly stored as **naive UTC** (e.g. ``datetime.utcnow()``).
For reporting and UI in IST, convert: attach UTC, convert to IST for display or calendar bounds.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def utc_now_naive() -> datetime:
    """Current instant as naive UTC (matches legacy ``datetime.utcnow()`` storage)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ist_now() -> datetime:
    """Timezone-aware current time in Asia/Kolkata."""
    return datetime.now(IST)


def ist_today() -> date:
    """Current calendar date in IST."""
    return ist_now().date()


def ist_calendar_bounds_as_utc_naive(start_d: date, end_d: date) -> tuple[datetime, datetime]:
    """
    Inclusive IST calendar range as naive UTC instants for SQL against UTC-naive columns.

    start_d 00:00 IST → UTC
    end_d   23:59:59.999999 IST → UTC
    """
    start_ist = datetime.combine(start_d, time.min, tzinfo=IST)
    end_ist = datetime.combine(end_d, time(23, 59, 59, 999999), tzinfo=IST)
    since = start_ist.astimezone(timezone.utc).replace(tzinfo=None)
    until = end_ist.astimezone(timezone.utc).replace(tzinfo=None)
    return since, until


def naive_db_datetime_to_ist_date(dt: datetime) -> date:
    """
    Interpret naive ``datetime`` from DB as UTC instant, return the calendar date in IST.
    If ``dt`` is already aware, convert from its zone to IST date.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(IST).date()
    return dt.replace(tzinfo=timezone.utc).astimezone(IST).date()


def format_utc_naive_window_as_ist(since: datetime, until: datetime) -> str:
    """Human-readable IST range for a naive-UTC instant pair."""
    s = since.replace(tzinfo=timezone.utc).astimezone(IST)
    u = until.replace(tzinfo=timezone.utc).astimezone(IST)
    return f"{s.strftime('%Y-%m-%d %H:%M')} → {u.strftime('%Y-%m-%d %H:%M')} IST"


def ist_date_span_days(since_utc_naive: datetime, until_utc_naive: datetime) -> int:
    """Inclusive IST calendar span as (end_date - start_date).days in IST."""
    s = since_utc_naive.replace(tzinfo=timezone.utc).astimezone(IST).date()
    u = until_utc_naive.replace(tzinfo=timezone.utc).astimezone(IST).date()
    return (u - s).days
