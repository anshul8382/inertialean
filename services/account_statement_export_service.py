"""
Export bank statement transactions for a user with classification columns.
Period presets: calendar months, rolling windows, Indian financial year (Apr–Mar).
"""
from __future__ import annotations

import calendar
import csv
import io
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from extensions import db
from models import AccountHead, BankStatement, BankStatementTransaction


def _last_day_of_month(d: date) -> date:
    _, last = calendar.monthrange(d.year, d.month)
    return date(d.year, d.month, last)


def fy_start(d: date) -> date:
    """Indian FY starts 1 April."""
    if d.month >= 4:
        return date(d.year, 4, 1)
    return date(d.year - 1, 4, 1)


def fy_end_for_start(start: date) -> date:
    return date(start.year + 1, 3, 31)


def resolve_period(preset: str, today: Optional[date] = None) -> Optional[Tuple[date, date, str]]:
    """
    Return (date_from, date_to, label) or None if unknown preset.
    `today` is injectable for tests.
    """
    today = today or date.today()
    preset = (preset or "").strip().lower().replace("-", "_")

    if preset == "current_month":
        start = date(today.year, today.month, 1)
        end = _last_day_of_month(today)
        return start, end, f"Current month ({start:%b %Y})"

    if preset == "prev_month":
        if today.month == 1:
            start = date(today.year - 1, 12, 1)
            end = date(today.year - 1, 12, 31)
        else:
            start = date(today.year, today.month - 1, 1)
            end = _last_day_of_month(start)
        return start, end, f"Previous month ({start:%b %Y})"

    if preset in ("last_7_days", "7_days", "week"):
        end = today
        start = today - timedelta(days=6)
        return start, end, "Last 7 days"

    if preset in ("last_30_days", "1_month", "rolling_30"):
        end = today
        start = today - timedelta(days=29)
        return start, end, "Last 30 days"

    if preset in ("last_90_days", "3_months", "rolling_90"):
        end = today
        start = today - timedelta(days=89)
        return start, end, "Last 90 days"

    if preset in ("last_180_days", "6_months", "rolling_180"):
        end = today
        start = today - timedelta(days=179)
        return start, end, "Last 180 days (~6 months)"

    if preset in ("fy_current", "current_fy", "fy"):
        start = fy_start(today)
        end_fy = fy_end_for_start(start)
        end = min(today, end_fy)
        return start, end, f"Current FY YTD ({start:%d %b %Y} – {end:%d %b %Y})"

    if preset in ("fy_previous", "previous_fy", "last_fy"):
        cur_start = fy_start(today)
        prev_end = cur_start - timedelta(days=1)
        prev_start = date(prev_end.year - 1, 4, 1)
        return prev_start, prev_end, f"Previous FY ({prev_start:%b %Y} – {prev_end:%b %Y})"

    if preset in ("last_6_months", "six_months"):
        end = today
        y, m = today.year, today.month
        m -= 5
        while m < 1:
            m += 12
            y -= 1
        start = date(y, m, 1)
        return start, end, "Last 6 months (from month start)"

    if preset in ("last_12_months", "twelve_months", "12_months"):
        end = today
        y, m = today.year, today.month
        m -= 11
        while m < 1:
            m += 12
            y -= 1
        start = date(y, m, 1)
        return start, end, "Last 12 months (from month start)"

    if preset in ("calendar_ytd", "year_to_date", "current_calendar_year", "this_year"):
        start = date(today.year, 1, 1)
        end = today
        return start, end, f"Calendar year to date ({today.year})"

    if preset in ("prev_calendar_year", "previous_calendar_year", "last_calendar_year"):
        y = today.year - 1
        return date(y, 1, 1), date(y, 12, 31), f"Previous calendar year ({y})"

    return None


def parse_custom_range(
    date_from_s: Optional[str], date_to_s: Optional[str], today: Optional[date] = None
) -> Optional[Tuple[date, date, str]]:
    """Parse YYYY-MM-DD strings; return None on error."""
    today = today or date.today()
    if not date_from_s or not date_to_s:
        return None
    try:
        parts_from = [int(x) for x in date_from_s.strip().split("-")]
        parts_to = [int(x) for x in date_to_s.strip().split("-")]
        if len(parts_from) != 3 or len(parts_to) != 3:
            return None
        y1, m1, d1 = parts_from
        y2, m2, d2 = parts_to
        start = date(y1, m1, d1)
        end = date(y2, m2, d2)
    except (ValueError, TypeError):
        return None
    if start > end:
        return None
    max_span = 366 * 6
    if (end - start).days > max_span:
        return None
    if end > today + timedelta(days=365):
        return None
    return start, end, f"Custom ({start} to {end})"


def fetch_transactions_for_user(user_id: int, date_from: date, date_to: date) -> List[BankStatementTransaction]:
    """All transactions in range for statements owned by user, ordered by date."""
    q = (
        BankStatementTransaction.query.join(BankStatement)
        .filter(
            BankStatement.created_by == user_id,
            BankStatementTransaction.transaction_date >= date_from,
            BankStatementTransaction.transaction_date <= date_to,
        )
        .options(
            joinedload(BankStatementTransaction.bank_statement),
            joinedload(BankStatementTransaction.income_expense_head),
        )
        .order_by(BankStatementTransaction.transaction_date, BankStatementTransaction.id)
    )
    return q.all()


def user_transaction_date_bounds(user_id: int) -> Optional[Tuple[date, date]]:
    """Earliest and latest transaction_date across the user's uploaded statements."""
    row = (
        db.session.query(
            func.min(BankStatementTransaction.transaction_date),
            func.max(BankStatementTransaction.transaction_date),
        )
        .join(BankStatement)
        .filter(BankStatement.created_by == user_id)
        .one()
    )
    earliest, latest = row[0], row[1]
    if earliest is None or latest is None:
        return None
    return earliest, latest


def browse_transactions_for_user(
    user_id: int,
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    statement_id: Optional[int] = None,
    head_id: Optional[int] = None,
    classified: Optional[str] = None,
    flow: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    per_page: int = 100,
) -> Dict[str, Any]:
    """
    Paginated browse of all uploaded bank transactions for a user with basic filters.

    classified: 'yes' | 'no' | None (all)
    flow: 'credit' | 'debit' | None (all) — credit = deposit>0, debit = withdrawal>0
    """
    from sqlalchemy import or_

    page = max(1, int(page or 1))
    per_page = max(1, min(int(per_page or 100), 500))

    base = BankStatementTransaction.query.join(BankStatement).filter(
        BankStatement.created_by == user_id
    )
    if date_from is not None:
        base = base.filter(BankStatementTransaction.transaction_date >= date_from)
    if date_to is not None:
        base = base.filter(BankStatementTransaction.transaction_date <= date_to)
    if statement_id is not None:
        base = base.filter(BankStatementTransaction.bank_statement_id == statement_id)
    if head_id is not None:
        base = base.filter(BankStatementTransaction.income_expense_head_id == head_id)

    classified_norm = (classified or "").strip().lower()
    if classified_norm in ("yes", "classified", "1", "true"):
        base = base.filter(BankStatementTransaction.income_expense_head_id.isnot(None))
    elif classified_norm in ("no", "unclassified", "0", "false"):
        base = base.filter(BankStatementTransaction.income_expense_head_id.is_(None))

    flow_norm = (flow or "").strip().lower()
    if flow_norm in ("credit", "deposit", "in"):
        base = base.filter(BankStatementTransaction.deposit_amount > 0)
    elif flow_norm in ("debit", "withdrawal", "out"):
        base = base.filter(BankStatementTransaction.withdrawal_amount > 0)

    q_text = (q or "").strip()
    if q_text:
        like = f"%{q_text}%"
        base = base.filter(
            or_(
                BankStatementTransaction.description.ilike(like),
                BankStatementTransaction.description_sanitized.ilike(like),
                BankStatementTransaction.reference_no.ilike(like),
            )
        )

    bounds_row = base.with_entities(
        func.min(BankStatementTransaction.transaction_date),
        func.max(BankStatementTransaction.transaction_date),
        func.count(BankStatementTransaction.id),
        func.coalesce(func.sum(BankStatementTransaction.deposit_amount), 0),
        func.coalesce(func.sum(BankStatementTransaction.withdrawal_amount), 0),
    ).one()
    filtered_earliest, filtered_latest = bounds_row[0], bounds_row[1]
    total_count = int(bounds_row[2] or 0)
    total_credit = float(bounds_row[3] or 0)
    total_debit = float(bounds_row[4] or 0)

    pages = max(1, (total_count + per_page - 1) // per_page) if total_count else 1
    if page > pages:
        page = pages
    offset = (page - 1) * per_page

    rows = (
        base.options(
            joinedload(BankStatementTransaction.bank_statement),
            joinedload(BankStatementTransaction.income_expense_head),
        )
        .order_by(
            BankStatementTransaction.transaction_date.desc(),
            BankStatementTransaction.id.desc(),
        )
        .offset(offset)
        .limit(per_page)
        .all()
    )

    return {
        "rows": rows,
        "total_count": total_count,
        "page": page,
        "per_page": per_page,
        "pages": pages,
        "filtered_earliest": filtered_earliest,
        "filtered_latest": filtered_latest,
        "total_credit": total_credit,
        "total_debit": total_debit,
    }


def build_csv_bytes(rows: List[BankStatementTransaction], period_label: str) -> bytes:
    """UTF-8 CSV with BOM for Excel; includes classification columns."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "period_note",
            "statement_id",
            "account_name",
            "statement_upload_date",
            "transaction_date",
            "value_date",
            "reference_no",
            "description",
            "description_sanitized",
            "withdrawal_amount",
            "deposit_amount",
            "running_balance",
            "classification_head",
            "classification_head_type",
            "classified",
        ]
    )
    for t in rows:
        stmt = t.bank_statement
        head: Optional[AccountHead] = t.income_expense_head
        w.writerow(
            [
                period_label,
                stmt.id if stmt else "",
                (stmt.account_name or "") if stmt else "",
                stmt.upload_date.strftime("%Y-%m-%d %H:%M") if stmt and stmt.upload_date else "",
                t.transaction_date.isoformat() if t.transaction_date else "",
                t.value_date.isoformat() if t.value_date else "",
                t.reference_no or "",
                t.description or "",
                t.description_sanitized or "",
                str(t.withdrawal_amount or 0),
                str(t.deposit_amount or 0),
                str(t.running_balance) if t.running_balance is not None else "",
                head.name if head else "",
                (head.head_type or "") if head else "",
                "yes" if t.income_expense_head_id else "no",
            ]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _shift_date_years(d: date, years: int) -> date:
    """Shift a calendar date by N years; clamp Feb 29 → Feb 28 when needed."""
    try:
        return date(d.year + years, d.month, d.day)
    except ValueError:
        # 29 Feb → 28 Feb in non-leap target year
        return date(d.year + years, d.month, 28)


def prior_year_period(
    date_from: date, date_to: date
) -> Tuple[date, date, str]:
    """
    Same calendar window one year earlier.
    Example: FY window 2026-04-01 → 2026-06-30 → 2025-04-01 → 2025-06-30.
    """
    p_from = _shift_date_years(date_from, -1)
    p_to = _shift_date_years(date_to, -1)
    if p_from > p_to:
        p_from, p_to = p_to, p_from
    label = f"Same period last year ({p_from:%d %b %Y} – {p_to:%d %b %Y})"
    return p_from, p_to, label


def _pct_change(current: float, prior: float) -> Optional[float]:
    if prior == 0:
        return None  # UI shows "new" / "—" when prior is zero
    return ((current - prior) / abs(prior)) * 100.0


def build_yoy_head_rows(
    current_rows: List[Dict[str, Any]],
    prior_rows: List[Dict[str, Any]],
    *,
    current_uncl_total: float = 0.0,
    prior_uncl_total: float = 0.0,
    current_uncl_count: int = 0,
    prior_uncl_count: int = 0,
) -> List[Dict[str, Any]]:
    """Merge this-period and prior-period head breakdowns by head_id (name fallback)."""
    cur_map: Dict[Any, Dict[str, Any]] = {}
    for r in current_rows:
        key = r.get("head_id") if r.get("head_id") is not None else ("name", r.get("name"))
        cur_map[key] = r
    pri_map: Dict[Any, Dict[str, Any]] = {}
    for r in prior_rows:
        key = r.get("head_id") if r.get("head_id") is not None else ("name", r.get("name"))
        pri_map[key] = r

    keys = list(dict.fromkeys(list(cur_map.keys()) + list(pri_map.keys())))
    out: List[Dict[str, Any]] = []
    for key in keys:
        c = cur_map.get(key) or {}
        p = pri_map.get(key) or {}
        cur_total = float(c.get("total") or 0)
        pri_total = float(p.get("total") or 0)
        delta = cur_total - pri_total
        pct = _pct_change(cur_total, pri_total)
        out.append(
            {
                "head_id": c.get("head_id") if c else p.get("head_id"),
                "name": c.get("name") or p.get("name") or "—",
                "head_type": c.get("head_type") or p.get("head_type") or "",
                "current_total": cur_total,
                "prior_total": pri_total,
                "current_count": int(c.get("count") or 0),
                "prior_count": int(p.get("count") or 0),
                "delta": delta,
                "pct_change": pct,
                "is_new": pri_total == 0 and cur_total > 0,
                "is_gone": cur_total == 0 and pri_total > 0,
            }
        )
    if current_uncl_total or prior_uncl_total or current_uncl_count or prior_uncl_count:
        cur_u = float(current_uncl_total or 0)
        pri_u = float(prior_uncl_total or 0)
        out.append(
            {
                "head_id": None,
                "name": "Unclassified",
                "head_type": "",
                "current_total": cur_u,
                "prior_total": pri_u,
                "current_count": int(current_uncl_count or 0),
                "prior_count": int(prior_uncl_count or 0),
                "delta": cur_u - pri_u,
                "pct_change": _pct_change(cur_u, pri_u),
                "is_new": pri_u == 0 and cur_u > 0,
                "is_gone": cur_u == 0 and pri_u > 0,
                "is_unclassified": True,
            }
        )
    out.sort(key=lambda x: (-abs(x["current_total"]), -abs(x["prior_total"]), x["name"]))
    return out


def analyze_classifications(
    user_id: int, date_from: date, date_to: date, head_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Aggregate credits (deposits) and debits (withdrawals) by classification head for a user and period.
    Unclassified lines are reported separately.
    If head_id is set, only transactions classified with that head are included.
    """
    base_filters = [
        BankStatement.created_by == user_id,
        BankStatementTransaction.transaction_date >= date_from,
        BankStatementTransaction.transaction_date <= date_to,
    ]
    if head_id is not None:
        base_filters.append(BankStatementTransaction.income_expense_head_id == head_id)
    base_filters_t = tuple(base_filters)

    total_row = (
        db.session.query(
            func.coalesce(func.sum(BankStatementTransaction.deposit_amount), 0),
            func.coalesce(func.sum(BankStatementTransaction.withdrawal_amount), 0),
            func.count(BankStatementTransaction.id),
        )
        .select_from(BankStatementTransaction)
        .join(BankStatement)
        .filter(*base_filters_t)
        .one()
    )
    total_credit = float(total_row[0] or 0)
    total_debit = float(total_row[1] or 0)
    txn_count = int(total_row[2] or 0)

    credit_rows = (
        db.session.query(
            BankStatementTransaction.income_expense_head_id,
            func.coalesce(func.sum(BankStatementTransaction.deposit_amount), 0),
            func.count(BankStatementTransaction.id),
        )
        .select_from(BankStatementTransaction)
        .join(BankStatement)
        .filter(*base_filters_t, BankStatementTransaction.deposit_amount > 0)
        .group_by(BankStatementTransaction.income_expense_head_id)
        .all()
    )

    debit_rows = (
        db.session.query(
            BankStatementTransaction.income_expense_head_id,
            func.coalesce(func.sum(BankStatementTransaction.withdrawal_amount), 0),
            func.count(BankStatementTransaction.id),
        )
        .select_from(BankStatementTransaction)
        .join(BankStatement)
        .filter(*base_filters_t, BankStatementTransaction.withdrawal_amount > 0)
        .group_by(BankStatementTransaction.income_expense_head_id)
        .all()
    )

    head_ids = {r[0] for r in credit_rows if r[0]}
    head_ids |= {r[0] for r in debit_rows if r[0]}
    heads = {h.id: h for h in AccountHead.query.filter(AccountHead.id.in_(head_ids)).all()} if head_ids else {}

    def build_breakdown(rows: List[Tuple[Any, Any, Any]]) -> Tuple[List[Dict[str, Any]], float, int]:
        out: List[Dict[str, Any]] = []
        uncl_total = 0.0
        uncl_count = 0
        for head_id, amt, cnt in rows:
            amt_f = float(amt or 0)
            c = int(cnt or 0)
            if head_id is None:
                uncl_total += amt_f
                uncl_count += c
                continue
            h = heads.get(head_id)
            out.append(
                {
                    "head_id": head_id,
                    "name": h.name if h else f"Head #{head_id}",
                    "head_type": (h.head_type or "").lower() if h else "",
                    "total": amt_f,
                    "count": c,
                }
            )
        out.sort(key=lambda x: -x["total"])
        return out, uncl_total, uncl_count

    credit_by_head, uc_tot, uc_cnt = build_breakdown(credit_rows)
    debit_by_head, ud_tot, ud_cnt = build_breakdown(debit_rows)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "total_credit": total_credit,
        "total_debit": total_debit,
        "net": total_credit - total_debit,
        "transaction_count": txn_count,
        "credit_by_head": credit_by_head,
        "debit_by_head": debit_by_head,
        "unclassified_credit_total": uc_tot,
        "unclassified_debit_total": ud_tot,
        "unclassified_credit_count": uc_cnt,
        "unclassified_debit_count": ud_cnt,
        "classified_credit_total": sum(x["total"] for x in credit_by_head),
        "classified_debit_total": sum(x["total"] for x in debit_by_head),
    }


def _month_keys_between(d0: date, d1: date) -> List[str]:
    """Inclusive calendar months from d0 through d1, as 'YYYY-MM'."""
    if d0 > d1:
        return []
    y, m = d0.year, d0.month
    end_y, end_m = d1.year, d1.month
    keys: List[str] = []
    while (y < end_y) or (y == end_y and m <= end_m):
        keys.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return keys


def list_heads_used_in_period(user_id: int, date_from: date, date_to: date) -> List[Dict[str, Any]]:
    """Distinct classification heads that appear on the user's transactions in the date range."""
    q = (
        db.session.query(BankStatementTransaction.income_expense_head_id)
        .select_from(BankStatementTransaction)
        .join(BankStatement)
        .filter(
            BankStatement.created_by == user_id,
            BankStatementTransaction.transaction_date >= date_from,
            BankStatementTransaction.transaction_date <= date_to,
            BankStatementTransaction.income_expense_head_id.isnot(None),
        )
        .distinct()
    )
    ids = [r[0] for r in q.all() if r[0]]
    if not ids:
        return []
    heads = AccountHead.query.filter(AccountHead.id.in_(ids)).order_by(AccountHead.name).all()
    return [{"id": h.id, "name": h.name, "head_type": (h.head_type or "").lower()} for h in heads]


def monthwise_flow_for_head(user_id: int, date_from: date, date_to: date, head_id: int) -> Dict[str, Any]:
    """
    Per calendar month: deposits and withdrawals attributed to a single head (how the head moves over time).
    """
    months = _month_keys_between(date_from, date_to)
    month_labels = []
    for mk in months:
        y, mo = (int(mk[:4]), int(mk[5:7]))
        month_labels.append(date(y, mo, 1).strftime("%b %Y"))

    ym_expr = func.date_format(BankStatementTransaction.transaction_date, "%Y-%m")
    head = AccountHead.query.get(head_id)
    head_name = head.name if head else f"Head #{head_id}"
    head_type = (head.head_type or "").lower() if head else ""

    base = (
        BankStatement.created_by == user_id,
        BankStatementTransaction.transaction_date >= date_from,
        BankStatementTransaction.transaction_date <= date_to,
        BankStatementTransaction.income_expense_head_id == head_id,
    )

    cr_rows = {
        str(r[0]): float(r[1] or 0)
        for r in db.session.query(ym_expr, func.coalesce(func.sum(BankStatementTransaction.deposit_amount), 0))
        .select_from(BankStatementTransaction)
        .join(BankStatement)
        .filter(*base, BankStatementTransaction.deposit_amount > 0)
        .group_by(ym_expr)
        .all()
        if r[0] is not None
    }
    dr_rows = {
        str(r[0]): float(r[1] or 0)
        for r in db.session.query(ym_expr, func.coalesce(func.sum(BankStatementTransaction.withdrawal_amount), 0))
        .select_from(BankStatementTransaction)
        .join(BankStatement)
        .filter(*base, BankStatementTransaction.withdrawal_amount > 0)
        .group_by(ym_expr)
        .all()
        if r[0] is not None
    }

    credit_data = [round(cr_rows.get(mk, 0.0), 2) for mk in months]
    debit_data = [round(dr_rows.get(mk, 0.0), 2) for mk in months]

    pc = sum(credit_data)
    pd = sum(debit_data)
    txn_count = (
        BankStatementTransaction.query.join(BankStatement)
        .filter(*base)
        .count()
    )

    return {
        "head_id": head_id,
        "head_name": head_name,
        "head_type": head_type,
        "month_labels": month_labels,
        "credit_data": credit_data,
        "debit_data": debit_data,
        "period_credit_total": round(pc, 2),
        "period_debit_total": round(pd, 2),
        "txn_count": txn_count,
        "has_data": (pc > 0 or pd > 0) and len(months) > 0,
    }


def monthwise_debit_expenses_by_head(
    user_id: int, date_from: date, date_to: date, head_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Debit (withdrawal) totals per calendar month per classification head.
    Uses MySQL DATE_FORMAT for grouping. Returns Chart.js-friendly stacked-bar payload.
    If head_id is set, only that head's debits are included (single-series stack).
    """
    months = _month_keys_between(date_from, date_to)
    month_labels = []
    for mk in months:
        y, mo = (int(mk[:4]), int(mk[5:7]))
        month_labels.append(date(y, mo, 1).strftime("%b %Y"))

    ym_expr = func.date_format(BankStatementTransaction.transaction_date, "%Y-%m")

    flt = [
        BankStatement.created_by == user_id,
        BankStatementTransaction.transaction_date >= date_from,
        BankStatementTransaction.transaction_date <= date_to,
        BankStatementTransaction.withdrawal_amount > 0,
    ]
    if head_id is not None:
        flt.append(BankStatementTransaction.income_expense_head_id == head_id)

    rows = (
        db.session.query(
            ym_expr.label("ym"),
            BankStatementTransaction.income_expense_head_id,
            func.coalesce(func.sum(BankStatementTransaction.withdrawal_amount), 0),
        )
        .select_from(BankStatementTransaction)
        .join(BankStatement)
        .filter(*flt)
        .group_by(ym_expr, BankStatementTransaction.income_expense_head_id)
        .order_by(ym_expr)
        .all()
    )

    pivot: Dict[Tuple[str, Optional[int]], float] = {}
    head_grand: Dict[Optional[int], float] = {}
    for ym, head_id, amt in rows:
        if ym is None:
            continue
        ym_s = str(ym)
        a = float(amt or 0)
        pivot[(ym_s, head_id)] = pivot.get((ym_s, head_id), 0) + a
        head_grand[head_id] = head_grand.get(head_id, 0) + a

    classified_ids = [hid for hid in head_grand if hid is not None]
    heads_map = (
        {h.id: h for h in AccountHead.query.filter(AccountHead.id.in_(classified_ids)).all()} if classified_ids else {}
    )

    ordered_ids = sorted(classified_ids, key=lambda hid: -head_grand.get(hid, 0))

    datasets: List[Dict[str, Any]] = []
    expense_colors = [
        "#c0392b",
        "#e74c3c",
        "#d35400",
        "#e67e22",
        "#f39c12",
        "#922b21",
        "#cb4335",
        "#af601a",
        "#b9770e",
        "#a04000",
    ]
    for i, hid in enumerate(ordered_ids):
        h = heads_map.get(hid)
        label = h.name if h else f"Head #{hid}"
        datasets.append(
            {
                "label": label,
                "data": [round(pivot.get((mk, hid), 0.0), 2) for mk in months],
                "backgroundColor": expense_colors[i % len(expense_colors)],
            }
        )

    ucl_series = [round(pivot.get((mk, None), 0.0), 2) for mk in months]
    if sum(ucl_series) > 0:
        datasets.append(
            {
                "label": "Unclassified",
                "data": ucl_series,
                "backgroundColor": "#6c757d",
            }
        )

    total_debit = sum(head_grand.values())
    has_data = total_debit > 0 and len(months) > 0

    return {
        "month_keys": months,
        "month_labels": month_labels,
        "datasets": datasets,
        "has_data": has_data,
        "total_debit_in_chart": total_debit,
    }
