"""
Market-closed dates and missing-price gap rules for price-accuracy.

Weekends are always closed. User-confirmed holidays persist in
var/price_accuracy/market_holidays.json.

Valuation already uses nearby closes (a few days either side), so a 1–5
open-day hole is not flagged. More than 5 consecutive open days without a
close is flagged only when a trade falls in that hole. Holiday candidates
are weekdays when names that actually traded all have no close (market-open
days where some names have a close are not holidays).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

# Flag only when missing open days exceed this (5 days of hole is still OK).
MAX_IGNORABLE_OPEN_DAYS = 5
SPARSE_MIN_UNIVERSE = 5
# Kept for older JSON / tests; holiday list now requires zero closes among names that traded.
SPARSE_MOST_MISSING_RATIO = 0.5


def to_cal_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def is_weekend(value: Any) -> bool:
    d = to_cal_date(value)
    return bool(d and d.weekday() >= 5)


def _dir() -> Path:
    root = Path(__file__).resolve().parent.parent / "var" / "price_accuracy"
    root.mkdir(parents=True, exist_ok=True)
    return root


def holidays_path() -> Path:
    return _dir() / "market_holidays.json"


def load_holiday_pack() -> Dict[str, Any]:
    path = holidays_path()
    if not path.is_file():
        return {"schema_version": 1, "dates": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        data.setdefault("schema_version", 1)
        data.setdefault("dates", {})
        if not isinstance(data["dates"], dict):
            data["dates"] = {}
        return data
    except Exception as exc:
        logger.warning("Failed reading market holidays JSON: %s", exc)
        return {"schema_version": 1, "dates": {}}


def save_holiday_pack(pack: Dict[str, Any]) -> Path:
    pack = dict(pack)
    pack["updated_at"] = datetime.utcnow().isoformat() + "Z"
    path = holidays_path()
    path.write_text(json.dumps(pack, indent=2, default=str), encoding="utf-8")
    return path


def load_holiday_dates() -> Set[date]:
    pack = load_holiday_pack()
    out: Set[date] = set()
    for key in (pack.get("dates") or {}):
        d = to_cal_date(key)
        if d:
            out.add(d)
    return out


def add_market_holiday(
    value: Any,
    *,
    note: str = "",
    source: str = "ui",
    persist: bool = True,
    pack: Optional[Dict[str, Any]] = None,
) -> Optional[date]:
    d = to_cal_date(value)
    if not d:
        return None
    pack = pack if pack is not None else load_holiday_pack()
    pack.setdefault("dates", {})[d.isoformat()] = {
        "note": (note or "")[:200],
        "source": (source or "ui")[:40],
        "confirmed_at": datetime.utcnow().isoformat() + "Z",
    }
    if persist:
        save_holiday_pack(pack)
    return d


def is_closed_calendar_day(
    value: Any,
    *,
    holiday_dates: Optional[Set[date]] = None,
    dates_with_close: Optional[Set[date]] = None,
) -> bool:
    """Weekend, user holiday, or (optional) day with no firm-wide close at all."""
    d = to_cal_date(value)
    if not d:
        return False
    if is_weekend(d):
        return True
    holidays = holiday_dates if holiday_dates is not None else load_holiday_dates()
    if d in holidays:
        return True
    if dates_with_close is not None and d not in dates_with_close:
        return True
    return False


def next_open_weekday(value: Any, closed: Set[date]) -> Optional[date]:
    d = to_cal_date(value)
    if not d:
        return None
    x = d + timedelta(days=1)
    for _ in range(14):
        if x.weekday() < 5 and x not in closed:
            return x
        x += timedelta(days=1)
    return x


def open_weekdays_between(start_exclusive: date, end_exclusive: date, closed: Set[date]) -> int:
    """Count weekdays strictly after start and before end that are not closed."""
    n = 0
    d = start_exclusive + timedelta(days=1)
    while d < end_exclusive:
        if d.weekday() < 5 and d not in closed:
            n += 1
        d += timedelta(days=1)
    return n


def first_open_weekday_after(start_exclusive: date, end_exclusive: date, closed: Set[date]) -> Optional[date]:
    d = start_exclusive + timedelta(days=1)
    while d < end_exclusive:
        if d.weekday() < 5 and d not in closed:
            return d
        d += timedelta(days=1)
    return None


def _trade_in_open_gap(
    trade_dates: Set[date],
    *,
    prev: Optional[date],
    nxt: Optional[date],
    range_start: date,
    range_end: date,
    have: Set[date],
) -> bool:
    """True if a trade sits in the hole (no stored close) between prev and nxt."""
    for t in trade_dates:
        if t < range_start or t > range_end or t in have:
            continue
        if prev is not None and t <= prev:
            continue
        if nxt is not None and t >= nxt:
            continue
        return True
    return False


def long_price_gaps(
    have: Iterable[Any],
    *,
    range_start: date,
    range_end: date,
    closed: Optional[Set[date]] = None,
    max_ignorable: int = MAX_IGNORABLE_OPEN_DAYS,
    trade_dates: Optional[Iterable[Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Gaps in a stock's close series longer than ``max_ignorable`` open days.

    Open days = weekdays that are not in ``closed`` (holidays / sparse market days).
    When ``trade_dates`` is set, only holes that contain a trade (with no close
    on that trade date) are returned — we do not need history on idle days.
    """
    closed = closed or set()
    have_set = {
        to_cal_date(x)
        for x in have
        if to_cal_date(x) and range_start <= to_cal_date(x) <= range_end
    }
    dates = sorted(have_set)
    trades: Optional[Set[date]] = None
    if trade_dates is not None:
        trades = {to_cal_date(x) for x in trade_dates}
        trades.discard(None)
    out: List[Dict[str, Any]] = []

    def _emit(prev: Optional[date], nxt: Optional[date], first_missing: Optional[date], n: int) -> None:
        if n <= max_ignorable or not first_missing:
            return
        if trades is not None and not _trade_in_open_gap(
            trades,
            prev=prev,
            nxt=nxt,
            range_start=range_start,
            range_end=range_end,
            have=have_set,
        ):
            return
        out.append(
            {
                "date_prev": prev,
                "date_next": nxt,
                "first_missing": first_missing,
                "open_days_missing": n,
            }
        )

    if not dates:
        n = open_weekdays_between(range_start - timedelta(days=1), range_end + timedelta(days=1), closed)
        first = first_open_weekday_after(range_start - timedelta(days=1), range_end + timedelta(days=1), closed)
        _emit(None, None, first or range_start, n)
        return out

    n = open_weekdays_between(range_start - timedelta(days=1), dates[0], closed)
    first = first_open_weekday_after(range_start - timedelta(days=1), dates[0], closed)
    _emit(None, dates[0], first, n)

    for prev, nxt in zip(dates, dates[1:]):
        n = open_weekdays_between(prev, nxt, closed)
        first = first_open_weekday_after(prev, nxt, closed)
        _emit(prev, nxt, first, n)

    n = open_weekdays_between(dates[-1], range_end + timedelta(days=1), closed)
    first = first_open_weekday_after(dates[-1], range_end + timedelta(days=1), closed)
    _emit(dates[-1], None, first, n)
    return out


def detect_sparse_weekdays(
    coverage: Sequence[Dict[str, Any]],
    *,
    holiday_dates: Optional[Set[date]] = None,
    min_universe: int = SPARSE_MIN_UNIVERSE,
    most_missing_ratio: float = SPARSE_MOST_MISSING_RATIO,
    require_trade: bool = True,
) -> List[Dict[str, Any]]:
    """
    Weekdays where names that traded all have no close (likely holiday / outage).

    Each coverage row: mn, mx, have (set of dates), optional symbol, optional trades.
    ``most_missing_ratio`` is unused when require_trade is True (kept for callers).
    """
    holiday_dates = holiday_dates or set()
    if not coverage:
        return []
    _ = most_missing_ratio
    mn = min(row["mn"] for row in coverage)
    mx = max(row["mx"] for row in coverage)
    out: List[Dict[str, Any]] = []
    d = mn
    while d <= mx:
        if d.weekday() < 5 and d not in holiday_dates:
            if require_trade:
                univ = [
                    row
                    for row in coverage
                    if row["mn"] <= d <= row["mx"] and d in (row.get("trades") or ())
                ]
            else:
                univ = [row for row in coverage if row["mn"] <= d <= row["mx"]]
            if len(univ) >= min_universe:
                have_n = sum(1 for row in univ if d in row["have"])
                missing_n = len(univ) - have_n
                # Any close among names that traded that day ⇒ market was open.
                if have_n == 0:
                    samples = sorted(
                        {
                            (row.get("symbol") or "").strip()
                            for row in univ
                            if d not in row["have"] and (row.get("symbol") or "").strip()
                        }
                    )[:8]
                    out.append(
                        {
                            "date": d.isoformat(),
                            "weekday": d.strftime("%A"),
                            "symbol_count": missing_n,
                            "universe": len(univ),
                            "have_close": have_n,
                            "finding_count": missing_n,
                            "sample_symbols": samples,
                        }
                    )
        d += timedelta(days=1)
    return out


def holiday_candidate_row_is_actionable(
    row: Dict[str, Any],
    *,
    traded_dates: Optional[Set[date]] = None,
) -> bool:
    """
    Keep a saved holiday candidate only if the market looks closed that day
    and (when known) someone in the firm actually traded.
    """
    d = to_cal_date(row.get("date"))
    if not d or is_weekend(d):
        return False
    universe = int(row.get("universe") or 0)
    missing_n = int(row.get("symbol_count") or row.get("finding_count") or 0)
    if universe <= 0:
        universe = missing_n
    have_close = row.get("have_close")
    if have_close is None:
        have_close = max(universe - missing_n, 0)
    try:
        have_close_i = int(have_close)
    except (TypeError, ValueError):
        have_close_i = 0
    if have_close_i > 0:
        return False
    if universe < SPARSE_MIN_UNIVERSE:
        return False
    if traded_dates is not None and d not in traded_dates:
        return False
    return True


def filter_holiday_candidates(
    rows: Sequence[Dict[str, Any]],
    *,
    traded_dates: Optional[Set[date]] = None,
) -> List[Dict[str, Any]]:
    return [
        r
        for r in (rows or [])
        if isinstance(r, dict) and holiday_candidate_row_is_actionable(r, traded_dates=traded_dates)
    ]


def sparse_candidates_path() -> Path:
    return _dir() / "last_sparse_days.json"


def save_sparse_candidates(rows: List[Dict[str, Any]]) -> Path:
    path = sparse_candidates_path()
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "dates": rows,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return path


def load_sparse_candidates() -> List[Dict[str, Any]]:
    path = sparse_candidates_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("dates") if isinstance(data, dict) else None
        return list(rows) if isinstance(rows, list) else []
    except Exception as exc:
        logger.warning("Failed reading sparse-day JSON: %s", exc)
        return []


def drop_sparse_candidate(value: Any) -> None:
    d = to_cal_date(value)
    if not d:
        return
    rows = [r for r in load_sparse_candidates() if to_cal_date(r.get("date")) != d]
    save_sparse_candidates(rows)


def missing_finding_is_noise(
    finding: Any,
    *,
    holiday_dates: Optional[Set[date]] = None,
    max_ignorable: int = MAX_IGNORABLE_OPEN_DAYS,
) -> bool:
    """True when a MISSING row should not be shown (weekend, holiday, or short gap)."""
    holidays = holiday_dates if holiday_dates is not None else load_holiday_dates()
    if isinstance(finding, dict):
        kind = str(finding.get("kind") or "").upper()
        md = to_cal_date(finding.get("missing_date"))
        dp = to_cal_date(finding.get("date_prev"))
        dn = to_cal_date(finding.get("date_next"))
    else:
        kind = str(getattr(finding, "kind", "") or "").upper()
        md = to_cal_date(getattr(finding, "missing_date", None))
        dp = to_cal_date(getattr(finding, "date_prev", None))
        dn = to_cal_date(getattr(finding, "date_next", None))
    if kind != "MISSING":
        return is_closed_calendar_day(md, holiday_dates=holidays) if md else False
    if md and is_closed_calendar_day(md, holiday_dates=holidays):
        return True
    if dp and dn:
        return open_weekdays_between(dp, dn, holidays) <= max_ignorable
    # Legacy one-date MISSING (trade date without exact close) — not a long gap.
    return True


def _dates_from_price_item(item: Dict[str, Any]) -> List[date]:
    raw = list(item.get("sample_dates") or [])
    out: List[date] = []
    seen = set()
    for value in raw:
        d = to_cal_date(value)
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    if out:
        return out
    import re

    for token in re.findall(r"\d{4}-\d{2}-\d{2}", str(item.get("title") or "")):
        d = to_cal_date(token)
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def snapshot_price_item_is_noise(
    item: Dict[str, Any],
    *,
    holiday_dates: Optional[Set[date]] = None,
) -> bool:
    """
    Nightly DI items are grouped (title + sample dates), not ORM rows.

    Legacy MISSING groups (no gap bounds) are one-day/weekend trade-date noise.
    Keep only MISSING rows that look like a long gap (date_prev + date_next).
    """
    kind = str(item.get("kind") or "").upper()
    if kind != "MISSING":
        return False
    holidays = holiday_dates if holiday_dates is not None else load_holiday_dates()
    if item.get("date_prev") and item.get("date_next"):
        return missing_finding_is_noise(item, holiday_dates=holidays)
    samples = _dates_from_price_item(item)
    if samples and all(is_closed_calendar_day(d, holiday_dates=holidays) for d in samples):
        return True
    # Grouped leftover from the old “every trade date without a close” scan.
    return True


def cluster_possible_holidays(
    missing_rows: List[Dict[str, Any]],
    *,
    min_symbols: int = 2,
) -> List[Dict[str, Any]]:
    """Fallback clustering from leftover MISSING rows (prefer load_sparse_candidates)."""
    by_date: Dict[date, Dict[str, Any]] = {}
    for row in missing_rows:
        d = to_cal_date(row.get("missing_date"))
        if not d or is_weekend(d):
            continue
        g = by_date.setdefault(d, {"date": d, "symbols": set(), "count": 0})
        g["count"] += 1
        sym = (row.get("symbol") or "").strip()
        if sym:
            g["symbols"].add(sym)
    out: List[Dict[str, Any]] = []
    for d, g in sorted(by_date.items()):
        n_sym = len(g["symbols"])
        if n_sym < min_symbols and g["count"] < min_symbols:
            continue
        out.append(
            {
                "date": d.isoformat(),
                "weekday": d.strftime("%A"),
                "symbol_count": n_sym,
                "finding_count": g["count"],
                "sample_symbols": sorted(g["symbols"])[:8],
            }
        )
    return out


def historical_close_dates_between(mn: date, mx: date) -> Set[date]:
    """Distinct calendar days that have at least one historical_price close in [mn, mx]."""
    from sqlalchemy import func

    from extensions import db
    from models import HistoricalPrice

    if not mn or not mx or mn > mx:
        return set()
    rows = (
        db.session.query(func.date(HistoricalPrice.date))
        .filter(HistoricalPrice.date >= mn, HistoricalPrice.date <= mx)
        .distinct()
        .all()
    )
    out: Set[date] = set()
    for (rd,) in rows:
        d = to_cal_date(rd)
        if d:
            out.add(d)
    return out


def dates_with_historical_close(candidates: Iterable[Any]) -> Set[date]:
    """Subset of ``candidates`` that have at least one historical_price row."""
    from sqlalchemy import func

    from extensions import db
    from models import HistoricalPrice

    dates = {to_cal_date(x) for x in candidates}
    dates.discard(None)
    if not dates:
        return set()
    rows = (
        db.session.query(func.date(HistoricalPrice.date))
        .filter(func.date(HistoricalPrice.date).in_(list(dates)))
        .distinct()
        .all()
    )
    out: Set[date] = set()
    for (rd,) in rows:
        d = to_cal_date(rd)
        if d:
            out.add(d)
    return out
