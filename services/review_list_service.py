"""
Classify review workflows for the Reviews board.

Buckets:
- open: status != closed
- due: open and review_date <= today (overdue + due today)
- upcoming: open and today < review_date <= today + 90 days (next quarter, rolling)
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional

UPCOMING_DAYS = 90
VALID_BUCKETS = ("board", "due", "upcoming", "open", "all")


def _is_closed(workflow) -> bool:
    return (getattr(workflow, "status", None) or "").strip().lower() == "closed"


def _client_name(workflow) -> str:
    client = getattr(workflow, "client", None)
    return (getattr(client, "name", None) or "").lower()


def _sort_by_review_date(rows: List[Any]) -> List[Any]:
    return sorted(rows, key=lambda w: (w.review_date, _client_name(w), getattr(w, "id", 0) or 0))


def classify_review_workflows(
    workflows: Iterable[Any],
    today: Optional[date] = None,
    upcoming_days: int = UPCOMING_DAYS,
) -> Dict[str, Any]:
    today = today or date.today()
    upcoming_end = today + timedelta(days=upcoming_days)
    open_w: List[Any] = []
    due_w: List[Any] = []
    upcoming_w: List[Any] = []

    for w in workflows:
        if _is_closed(w):
            continue
        rd = getattr(w, "review_date", None)
        if rd is None:
            continue
        open_w.append(w)
        if rd <= today:
            due_w.append(w)
        elif rd <= upcoming_end:
            upcoming_w.append(w)

    return {
        "open": _sort_by_review_date(open_w),
        "due": _sort_by_review_date(due_w),
        "upcoming": _sort_by_review_date(upcoming_w),
        "today": today,
        "upcoming_end": upcoming_end,
        "upcoming_days": upcoming_days,
    }


def normalize_bucket(raw: Optional[str]) -> str:
    bucket = (raw or "board").strip().lower()
    if bucket not in VALID_BUCKETS:
        return "board"
    return bucket
