"""Working-time helpers for SLA clocks.

CAPTURED 2026-09: Saturday counts as a working day; Sunday does not.
"""
from __future__ import annotations

from datetime import datetime, timedelta


def working_hours_since(start: datetime, now: datetime | None = None) -> float:
    """Elapsed working hours from start to now (Sunday excluded; Sat included)."""
    now = now or datetime.utcnow()
    if start.tzinfo is not None:
        start = start.replace(tzinfo=None)
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    if start >= now:
        return 0.0
    hours = 0.0
    cursor = start
    # Cap: 120 calendar days
    for _ in range(120 * 24):
        if cursor >= now:
            break
        # weekday(): Mon=0 … Sat=5, Sun=6
        if cursor.weekday() != 6:
            hours += 1.0
        cursor += timedelta(hours=1)
    return hours


def wall_hours_since(start: datetime, now: datetime | None = None) -> float:
    """Elapsed wall-clock hours (no Sunday skip) — used by A3 1-hour SLA."""
    now = now or datetime.utcnow()
    if start.tzinfo is not None:
        start = start.replace(tzinfo=None)
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    if start >= now:
        return 0.0
    return (now - start).total_seconds() / 3600.0
