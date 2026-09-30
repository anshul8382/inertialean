"""
Classify review workflows for the Reviews board and digests.

Horizon: through end of next calendar month (nothing further out on the board).

Sections:
1. current — in progress (sent/meeting) and due (<= today)
2. priority — initiated and due (<= today)
3. this_month — due from tomorrow through month-end
4. next_month — due in next calendar month
5. suggested_close — obsolete older open rows vs the client's current review
6. date_missing — open rows with no review_date (bottom)

One primary review per client in sections 1–4; superseded older open rows → §5.
Forward planned rows beyond the current primary (still in-horizon) are hidden.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple

OPEN_STATUSES = frozenset({"initiated", "sent", "meeting"})
IN_PROGRESS_STATUSES = frozenset({"sent", "meeting"})
VALID_BUCKETS = (
    "board",
    "report",  # alias of board — full digest view
    "schedules",
    "current",
    "priority",
    "this_month",
    "next_month",
    "suggested_close",
    "date_missing",
    "due",  # alias: current + priority
    "upcoming",  # alias: this_month + next_month
    "open",
    "all",
)

# Backward-compat name used by older callers/tests
UPCOMING_DAYS = 60


def _status(workflow) -> str:
    return (getattr(workflow, "status", None) or "").strip().lower()


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
    # None dates sort last when mixed; dedicated date_missing list uses name/id
    return (
        rd is None,
        rd or date.max,
        _client_name(workflow),
        getattr(workflow, "id", 0) or 0,
    )


def _sort_rows(rows: List[Any]) -> List[Any]:
    return sorted(rows, key=_sort_key)


def _pick_current(dated: List[Any], today: date, month_end: date, next_end: date) -> Optional[Any]:
    """One primary actionable review for the client within the horizon."""
    dated_sorted = _sort_rows(dated)

    for w in dated_sorted:
        rd = w.review_date
        if rd <= today and _status(w) in IN_PROGRESS_STATUSES:
            return w

    for w in dated_sorted:
        rd = w.review_date
        if rd <= today and _status(w) == "initiated":
            return w

    for w in dated_sorted:
        rd = w.review_date
        if today < rd <= month_end:
            return w

    for w in dated_sorted:
        rd = w.review_date
        if month_end < rd <= next_end:
            return w

    return None


def _section_for_current(workflow, today: date, month_end: date, next_start: date, next_end: date) -> Optional[str]:
    rd = getattr(workflow, "review_date", None)
    if rd is None:
        return None
    st = _status(workflow)
    if rd <= today and st in IN_PROGRESS_STATUSES:
        return "current"
    if rd <= today and st == "initiated":
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
    priority: List[Any] = []
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
            # All dated rows are beyond next month — leave them off the board
            continue

        section = _section_for_current(primary, today, month_end, next_start, next_end)
        if section == "current":
            current.append(primary)
        elif section == "priority":
            priority.append(primary)
        elif section == "this_month":
            this_month.append(primary)
        elif section == "next_month":
            next_month.append(primary)

        for w in dated:
            if w is primary or getattr(w, "id", None) == getattr(primary, "id", None):
                continue
            if w.review_date < primary.review_date:
                suggested_close.append(w)
            # later in-horizon / beyond-horizon planning rows: hidden

    due = _sort_rows(current + priority)
    upcoming = _sort_rows(this_month + next_month)
    open_board = _sort_rows(current + priority + this_month + next_month)

    return {
        "current": _sort_rows(current),
        "priority": _sort_rows(priority),
        "this_month": _sort_rows(this_month),
        "next_month": _sort_rows(next_month),
        "suggested_close": _sort_rows(suggested_close),
        "date_missing": sorted(
            date_missing,
            key=lambda w: (_client_name(w), getattr(w, "id", 0) or 0),
        ),
        # Compat aliases
        "due": due,
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
    if bucket not in VALID_BUCKETS:
        return "board"
    return bucket


def workflows_for_bucket(classified: Dict[str, Any], bucket: str) -> List[Any]:
    if bucket == "current":
        return classified["current"]
    if bucket == "priority":
        return classified["priority"]
    if bucket == "this_month":
        return classified["this_month"]
    if bucket == "next_month":
        return classified["next_month"]
    if bucket == "suggested_close":
        return classified["suggested_close"]
    if bucket == "date_missing":
        return classified["date_missing"]
    if bucket == "due":
        return classified["due"]
    if bucket == "upcoming":
        return classified["upcoming"]
    if bucket == "open":
        return classified["open"]
    return []
