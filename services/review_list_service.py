"""
Classify review workflows for the Reviews board and digests.

Horizon: through end of next calendar month (nothing further out on the board).

Sections:
1. current — in progress (sent/meeting)
2. due — status=due (system queue)
3. priority — initiated (in flight)
4. deferred — user acked delay
5. this_month / next_month — planned by date in horizon
6. suggested_close — obsolete older open rows vs the client's current review
7. date_missing — open rows with no review_date (bottom)

One primary review per client in board sections; superseded older open rows → suggested_close.
Forward planned rows beyond the current primary (still in-horizon) are hidden.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.review_status_service import (
    IN_PROGRESS_STATUSES,
    OPEN_STATUSES,
    STATUS_DEFERRED,
    STATUS_DUE,
    STATUS_INITIATED,
    STATUS_PLANNED,
    normalize_status,
)

__all__ = (
    "OPEN_STATUSES",
    "IN_PROGRESS_STATUSES",
    "VALID_BUCKETS",
    "UPCOMING_DAYS",
    "classify_review_workflows",
    "normalize_bucket",
    "workflows_for_bucket",
)

VALID_BUCKETS = (
    "board",
    "report",  # alias of board — full digest view
    "schedules",
    "current",
    "due",
    "priority",
    "initiated",  # alias of priority
    "deferred",
    "this_month",
    "next_month",
    "suggested_close",
    "date_missing",
    "upcoming",  # alias: this_month + next_month
    "open",
    "all",
)

# Backward-compat name used by older callers/tests
UPCOMING_DAYS = 60


def _status(workflow) -> str:
    return normalize_status(getattr(workflow, "status", None))


def _is_closed(workflow) -> bool:
    return _status(workflow) == "closed"


def _is_open(workflow) -> bool:
    return _status(workflow) in OPEN_STATUSES


def _client_id(workflow) -> Any:
    cid = getattr(workflow, "client_id", None)
    if cid is not None:
        return cid
    client = getattr(workflow, "client", None)
    return getattr(client, "id", None)


def _client_name(workflow) -> str:
    client = getattr(workflow, "client", None)
    return (getattr(client, "name", None) or "").lower()


def _month_end(d: date) -> date:
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def _next_month_bounds(today: date) -> Tuple[date, date]:
    if today.month == 12:
        start = date(today.year + 1, 1, 1)
    else:
        start = date(today.year, today.month + 1, 1)
    return start, _month_end(start)


def _sort_key(workflow) -> Tuple:
    rd = getattr(workflow, "review_date", None)
    return (
        rd is None,
        rd or date.max,
        _client_name(workflow),
        getattr(workflow, "id", 0) or 0,
    )


def _sort_rows(rows: List[Any]) -> List[Any]:
    return sorted(rows, key=_sort_key)


def _status_rank(st: str) -> int:
    """Lower = more actionable for primary pick."""
    if st in IN_PROGRESS_STATUSES:
        return 0
    if st == STATUS_DUE:
        return 1
    if st == STATUS_INITIATED:
        return 2
    if st == STATUS_DEFERRED:
        return 3
    if st == STATUS_PLANNED:
        return 4
    return 5


def _pick_current(
    dated: List[Any], today: date, month_end: date, next_end: date
) -> Optional[Any]:
    """One primary actionable review for the client within the horizon."""
    dated_sorted = _sort_rows(dated)

    # Prefer in-progress, then due, then initiated, then deferred, then planned —
    # among those with review_date <= next_end
    in_horizon = [w for w in dated_sorted if w.review_date <= next_end]
    if not in_horizon:
        return None

    in_horizon.sort(
        key=lambda w: (
            _status_rank(_status(w)),
            w.review_date or date.max,
            _client_name(w),
            getattr(w, "id", 0) or 0,
        )
    )
    return in_horizon[0]


def _section_for_primary(
    workflow, today: date, month_end: date, next_start: date, next_end: date
) -> Optional[str]:
    st = _status(workflow)
    rd = getattr(workflow, "review_date", None)
    if rd is None:
        return None

    if st in IN_PROGRESS_STATUSES:
        return "current"
    if st == STATUS_DUE:
        return "due"
    if st == STATUS_INITIATED:
        return "priority"
    if st == STATUS_DEFERRED:
        return "deferred"
    if st == STATUS_PLANNED:
        if today < rd <= month_end:
            return "this_month"
        if next_start <= rd <= next_end:
            return "next_month"
        if rd <= today:
            # Planned but past date — still show under this_month as actionable plan
            return "this_month"
        return None
    # Legacy / other open: bucket by date like before
    if rd <= today:
        return "priority"
    if today < rd <= month_end:
        return "this_month"
    if next_start <= rd <= next_end:
        return "next_month"
    return None


def classify_review_workflows(
    workflows: Iterable[Any],
    today: Optional[date] = None,
    upcoming_days: int = UPCOMING_DAYS,  # unused; kept for call-site compat
) -> Dict[str, Any]:
    today = today or date.today()
    month_end = _month_end(today)
    next_start, next_end = _next_month_bounds(today)

    by_client: Dict[Any, List[Any]] = {}
    for w in workflows:
        if _is_closed(w) or not _is_open(w):
            continue
        cid = _client_id(w)
        by_client.setdefault(cid, []).append(w)

    current: List[Any] = []
    due: List[Any] = []
    priority: List[Any] = []
    deferred: List[Any] = []
    this_month: List[Any] = []
    next_month: List[Any] = []
    suggested_close: List[Any] = []
    date_missing: List[Any] = []

    for _cid, rows in by_client.items():
        undated = [w for w in rows if getattr(w, "review_date", None) is None]
        dated = [w for w in rows if getattr(w, "review_date", None) is not None]
        date_missing.extend(undated)

        primary = _pick_current(dated, today, month_end, next_end)
        if primary is None:
            continue

        section = _section_for_primary(primary, today, month_end, next_start, next_end)
        if section == "current":
            current.append(primary)
        elif section == "due":
            due.append(primary)
        elif section == "priority":
            priority.append(primary)
        elif section == "deferred":
            deferred.append(primary)
        elif section == "this_month":
            this_month.append(primary)
        elif section == "next_month":
            next_month.append(primary)

        for w in dated:
            if w is primary or getattr(w, "id", None) == getattr(primary, "id", None):
                continue
            if w.review_date < primary.review_date:
                suggested_close.append(w)

    upcoming = _sort_rows(this_month + next_month)
    open_board = _sort_rows(
        current + due + priority + deferred + this_month + next_month
    )
    # Compat: "due" board alias used to mean current+priority; keep
    # `due_combined` for digests that want actionable non-deferred queue.
    due_combined = _sort_rows(current + due + priority)

    return {
        "current": _sort_rows(current),
        "due": _sort_rows(due),
        "priority": _sort_rows(priority),
        "deferred": _sort_rows(deferred),
        "this_month": _sort_rows(this_month),
        "next_month": _sort_rows(next_month),
        "suggested_close": _sort_rows(suggested_close),
        "date_missing": sorted(
            date_missing,
            key=lambda w: (_client_name(w), getattr(w, "id", 0) or 0),
        ),
        # Compat aliases
        "due_combined": due_combined,
        "upcoming": upcoming,
        "open": open_board,
        "today": today,
        "month_end": month_end,
        "next_month_start": next_start,
        "upcoming_end": next_end,
        "upcoming_days": (next_end - today).days,
    }


def normalize_bucket(raw: Optional[str]) -> str:
    bucket = (raw or "board").strip().lower()
    if bucket == "report":
        return "board"
    if bucket == "initiated":
        return "priority"
    if bucket not in VALID_BUCKETS:
        return "board"
    return bucket


def workflows_for_bucket(classified: Dict[str, Any], bucket: str) -> List[Any]:
    if bucket == "current":
        return classified["current"]
    if bucket in ("due",):
        return classified["due"]
    if bucket in ("priority", "initiated"):
        return classified["priority"]
    if bucket == "deferred":
        return classified["deferred"]
    if bucket == "this_month":
        return classified["this_month"]
    if bucket == "next_month":
        return classified["next_month"]
    if bucket == "suggested_close":
        return classified["suggested_close"]
    if bucket == "date_missing":
        return classified["date_missing"]
    if bucket == "upcoming":
        return classified["upcoming"]
    if bucket == "open":
        return classified["open"]
    return []
