"""
Per-advisor book metrics for admin Advisor Review.

AUM: holdings market value (same approach as Practice Analytics).
Committed monthly: sum of active MonthlyInvestmentSchedule.planned_amount.
Growth (MoM): this calendar month vs prior month sum of MonthlyInvestment.planned_amount
(by investment_date), with a short trend series for display.
Review pendency: open ReviewWorkflow statuses initiated/sent/meeting.
"""
from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from extensions import db
from models import (
    Client,
    Holding,
    MonthlyInvestment,
    MonthlyInvestmentSchedule,
    ReviewWorkflow,
    User,
)

OPEN_REVIEW_STATUSES = ("initiated", "sent", "meeting")
TREND_MONTHS = 6


def _as_float(x: Any) -> float:
    if x is None:
        return 0.0
    if isinstance(x, Decimal):
        return float(x)
    return float(x)


def format_inr_compact(value: float) -> str:
    """Display helper — no decimals for raw; compact for large amounts."""
    v = float(value or 0)
    sign = "-" if v < 0 else ""
    av = abs(v)
    if av >= 10_000_000:
        return f"{sign}₹{av / 10_000_000:.2f} Cr"
    if av >= 100_000:
        return f"{sign}₹{av / 100_000:.2f} Lakh"
    return f"{sign}₹{av:,.0f}"


def _month_bounds(d: date) -> Tuple[date, date]:
    last = monthrange(d.year, d.month)[1]
    return date(d.year, d.month, 1), date(d.year, d.month, last)


def _shift_month(d: date, months: int) -> date:
    """Return the 1st of the month offset by ``months`` (negative = past)."""
    y, m = d.year, d.month + months
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return date(y, m, 1)


def _month_label(d: date) -> str:
    return d.strftime("%b %Y")


def count_unassigned_active_clients() -> int:
    return (
        Client.query.filter(Client.is_active.is_(True), Client.advisor_id.is_(None)).count()
    )


def _active_clients_by_advisor() -> Dict[Optional[int], List[Client]]:
    clients = (
        Client.query.filter(Client.is_active.is_(True))
        .options(joinedload(Client.advisor))
        .order_by(Client.name)
        .all()
    )
    by: Dict[Optional[int], List[Client]] = defaultdict(list)
    for c in clients:
        by[c.advisor_id].append(c)
    return by


def _aum_by_client(client_ids: List[int]) -> Dict[int, float]:
    current_by: Dict[int, float] = defaultdict(float)
    if not client_ids:
        return {}
    holdings = (
        Holding.query.filter(Holding.client_id.in_(client_ids))
        .options(joinedload(Holding.security))
        .all()
    )
    for h in holdings:
        sec = h.security
        if not sec:
            continue
        price = _as_float(sec.current_price)
        if price <= 0:
            continue
        current_by[h.client_id] += _as_float(h.quantity) * price
    return dict(current_by)


def _committed_by_client(client_ids: List[int], as_of: date) -> Dict[int, float]:
    """Active schedule planned amounts keyed by client_id."""
    if not client_ids:
        return {}
    month_end = _month_bounds(as_of)[1]
    rows = (
        db.session.query(
            MonthlyInvestmentSchedule.client_id,
            MonthlyInvestmentSchedule.planned_amount,
        )
        .filter(
            MonthlyInvestmentSchedule.client_id.in_(client_ids),
            MonthlyInvestmentSchedule.is_active.is_(True),
            MonthlyInvestmentSchedule.start_date <= month_end,
            or_(
                MonthlyInvestmentSchedule.end_date.is_(None),
                MonthlyInvestmentSchedule.end_date >= as_of,
            ),
        )
        .all()
    )
    out: Dict[int, float] = {}
    for cid, amt in rows:
        out[cid] = _as_float(amt)
    return out


def _pending_reviews_by_client(client_ids: List[int]) -> Dict[int, int]:
    if not client_ids:
        return {}
    rows = (
        db.session.query(ReviewWorkflow.client_id, func.count(ReviewWorkflow.id))
        .filter(
            ReviewWorkflow.client_id.in_(client_ids),
            ReviewWorkflow.status.in_(OPEN_REVIEW_STATUSES),
        )
        .group_by(ReviewWorkflow.client_id)
        .all()
    )
    return {cid: int(cnt) for cid, cnt in rows}


def _planned_investment_by_client_month(
    client_ids: List[int],
    month_start: date,
    month_end: date,
) -> Dict[int, float]:
    """Sum MonthlyInvestment.planned_amount per client for investment_date in month."""
    if not client_ids:
        return {}
    rows = (
        db.session.query(
            MonthlyInvestment.client_id,
            func.coalesce(func.sum(MonthlyInvestment.planned_amount), 0),
        )
        .filter(
            MonthlyInvestment.client_id.in_(client_ids),
            MonthlyInvestment.investment_date >= month_start,
            MonthlyInvestment.investment_date <= month_end,
        )
        .group_by(MonthlyInvestment.client_id)
        .all()
    )
    return {cid: _as_float(total) for cid, total in rows}


def _growth_and_trend(
    this_amount: float,
    last_amount: float,
    trend_series: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if last_amount > 0:
        growth_pct = round((this_amount - last_amount) / last_amount * 100.0, 1)
    elif this_amount > 0:
        growth_pct = None  # new / from zero
    else:
        growth_pct = 0.0

    if last_amount <= 0 and this_amount <= 0:
        trend = "flat"
    elif last_amount <= 0 and this_amount > 0:
        trend = "up"
    elif this_amount > last_amount:
        trend = "up"
    elif this_amount < last_amount:
        trend = "down"
    else:
        trend = "flat"

    return {
        "this_month_planned": this_amount,
        "last_month_planned": last_amount,
        "growth_pct": growth_pct,
        "trend": trend,
        "trend_months": trend_series,
    }


def _build_trend_for_clients(
    client_ids: List[int],
    as_of: date,
    months: int = TREND_MONTHS,
) -> Tuple[float, float, List[Dict[str, Any]]]:
    """Return (this_month_total, last_month_total, trend list oldest→newest)."""
    series: List[Dict[str, Any]] = []
    totals: List[float] = []
    for i in range(months - 1, -1, -1):
        start = _shift_month(as_of, -i)
        end = _month_bounds(start)[1]
        by_client = _planned_investment_by_client_month(client_ids, start, end)
        total = sum(by_client.values())
        totals.append(total)
        series.append(
            {
                "key": start.strftime("%Y-%m"),
                "label": _month_label(start),
                "amount": total,
                "amount_display": format_inr_compact(total),
            }
        )
    if series:
        max_a = max(m["amount"] for m in series) or 0.0
        for m in series:
            if max_a <= 0:
                m["bar_pct"] = 8
                m["bar_px_sm"] = 4
                m["bar_px_lg"] = 8
            else:
                pct = max(8, int(round(100.0 * m["amount"] / max_a)))
                m["bar_pct"] = pct
                m["bar_px_sm"] = max(4, int(round(pct * 0.24)))
                m["bar_px_lg"] = max(8, int(round(pct * 0.48)))
    this_amount = totals[-1] if totals else 0.0
    last_amount = totals[-2] if len(totals) >= 2 else 0.0
    return this_amount, last_amount, series


def _advisor_display_name(user: Optional[User]) -> str:
    if not user:
        return "Unassigned"
    return user.username or user.email or f"User #{user.id}"


def get_advisor_book_rows(as_of: date | None = None) -> List[Dict[str, Any]]:
    """
    One row per advisor who has ≥1 active client, plus optional Unassigned row if any.
    Sorted by AUM descending.
    """
    as_of = as_of or date.today()
    by_advisor = _active_clients_by_advisor()
    all_ids = [c.id for clients in by_advisor.values() for c in clients]
    aum_by = _aum_by_client(all_ids)
    committed_by = _committed_by_client(all_ids, as_of)
    pending_by = _pending_reviews_by_client(all_ids)

    # Precompute MoM planned per client for this + last month and trend months
    this_start, this_end = _month_bounds(as_of)
    last_start = _shift_month(as_of, -1)
    last_end = _month_bounds(last_start)[1]
    this_planned_by = _planned_investment_by_client_month(all_ids, this_start, this_end)
    last_planned_by = _planned_investment_by_client_month(all_ids, last_start, last_end)

    rows: List[Dict[str, Any]] = []
    for advisor_id, clients in by_advisor.items():
        if advisor_id is None:
            continue
        advisor = clients[0].advisor if clients else None
        cids = [c.id for c in clients]
        aum = sum(aum_by.get(i, 0.0) for i in cids)
        committed = sum(committed_by.get(i, 0.0) for i in cids)
        pending = sum(pending_by.get(i, 0) for i in cids)
        this_amt = sum(this_planned_by.get(i, 0.0) for i in cids)
        last_amt = sum(last_planned_by.get(i, 0.0) for i in cids)
        _, _, trend_series = _build_trend_for_clients(cids, as_of)
        # Recompute this/last from trend_series to stay consistent
        if len(trend_series) >= 2:
            this_amt = trend_series[-1]["amount"]
            last_amt = trend_series[-2]["amount"]
        growth = _growth_and_trend(this_amt, last_amt, trend_series)
        rows.append(
            {
                "advisor_id": advisor_id,
                "advisor_name": _advisor_display_name(advisor),
                "client_count": len(clients),
                "aum": aum,
                "aum_display": format_inr_compact(aum),
                "pending_reviews": pending,
                "committed_monthly": committed,
                "committed_display": format_inr_compact(committed),
                **growth,
                "this_month_display": format_inr_compact(growth["this_month_planned"]),
                "last_month_display": format_inr_compact(growth["last_month_planned"]),
            }
        )

    rows.sort(key=lambda r: r["aum"], reverse=True)

    unassigned = by_advisor.get(None) or []
    if unassigned:
        cids = [c.id for c in unassigned]
        aum = sum(aum_by.get(i, 0.0) for i in cids)
        committed = sum(committed_by.get(i, 0.0) for i in cids)
        pending = sum(pending_by.get(i, 0) for i in cids)
        this_amt, last_amt, trend_series = _build_trend_for_clients(cids, as_of)
        growth = _growth_and_trend(this_amt, last_amt, trend_series)
        rows.append(
            {
                "advisor_id": None,
                "advisor_name": "Unassigned",
                "client_count": len(unassigned),
                "aum": aum,
                "aum_display": format_inr_compact(aum),
                "pending_reviews": pending,
                "committed_monthly": committed,
                "committed_display": format_inr_compact(committed),
                **growth,
                "this_month_display": format_inr_compact(growth["this_month_planned"]),
                "last_month_display": format_inr_compact(growth["last_month_planned"]),
                "is_unassigned": True,
            }
        )

    return rows


def get_advisor_detail(advisor_id: int, as_of: date | None = None) -> Optional[Dict[str, Any]]:
    """Detailed book for one advisor (admin drill-down)."""
    as_of = as_of or date.today()
    advisor = User.query.get(advisor_id)
    if not advisor:
        return None

    clients = (
        Client.query.filter(Client.is_active.is_(True), Client.advisor_id == advisor_id)
        .order_by(Client.name)
        .all()
    )
    cids = [c.id for c in clients]
    aum_by = _aum_by_client(cids)
    committed_by = _committed_by_client(cids, as_of)
    pending_by = _pending_reviews_by_client(cids)
    this_amt, last_amt, trend_series = _build_trend_for_clients(cids, as_of)
    growth = _growth_and_trend(this_amt, last_amt, trend_series)

    client_rows = []
    for c in clients:
        client_rows.append(
            {
                "id": c.id,
                "name": c.name,
                "aum": aum_by.get(c.id, 0.0),
                "aum_display": format_inr_compact(aum_by.get(c.id, 0.0)),
                "committed": committed_by.get(c.id, 0.0),
                "committed_display": format_inr_compact(committed_by.get(c.id, 0.0)),
                "pending_reviews": pending_by.get(c.id, 0),
            }
        )
    client_rows.sort(key=lambda r: r["aum"], reverse=True)

    return {
        "advisor_id": advisor_id,
        "advisor_name": _advisor_display_name(advisor),
        "client_count": len(clients),
        "aum": sum(aum_by.values()),
        "aum_display": format_inr_compact(sum(aum_by.values())),
        "pending_reviews": sum(pending_by.values()),
        "committed_monthly": sum(committed_by.values()),
        "committed_display": format_inr_compact(sum(committed_by.values())),
        **growth,
        "this_month_display": format_inr_compact(growth["this_month_planned"]),
        "last_month_display": format_inr_compact(growth["last_month_planned"]),
        "clients": client_rows,
        "as_of": as_of,
        "this_month_label": _month_label(as_of),
        "last_month_label": _month_label(_shift_month(as_of, -1)),
    }


def get_advisor_assignment_overview(
    *,
    advisor_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Advisor ↔ client roster (names only — no AUM / growth).

    If ``advisor_id`` is set, returns only that advisor's book (advisors'
    own-assignment view). Firm-wide unassigned list is omitted when scoped.
    """
    by_advisor = _active_clients_by_advisor()
    groups: List[Dict[str, Any]] = []
    for aid, clients in by_advisor.items():
        if aid is None:
            continue
        if advisor_id is not None and aid != advisor_id:
            continue
        advisor = clients[0].advisor if clients else None
        groups.append(
            {
                "advisor_id": aid,
                "advisor_name": _advisor_display_name(advisor),
                "client_count": len(clients),
                "clients": [{"id": c.id, "name": c.name} for c in clients],
            }
        )
    groups.sort(key=lambda g: g["advisor_name"].lower())

    if advisor_id is not None:
        # Own-book view: no firm-wide unassigned roster
        if not groups:
            user = User.query.get(advisor_id)
            groups = [
                {
                    "advisor_id": advisor_id,
                    "advisor_name": _advisor_display_name(user),
                    "client_count": 0,
                    "clients": [],
                }
            ]
        return {
            "groups": groups,
            "unassigned_count": 0,
            "unassigned_clients": [],
            "advisor_count": len(groups),
            "assigned_client_count": sum(g["client_count"] for g in groups),
            "scoped_to_self": True,
        }

    unassigned = by_advisor.get(None) or []
    return {
        "groups": groups,
        "unassigned_count": len(unassigned),
        "unassigned_clients": [{"id": c.id, "name": c.name} for c in unassigned],
        "advisor_count": len(groups),
        "assigned_client_count": sum(g["client_count"] for g in groups),
        "scoped_to_self": False,
    }


def get_dashboard_advisor_snapshot(as_of: date | None = None) -> Dict[str, Any]:
    """Compact payload for admin home dashboard."""
    as_of = as_of or date.today()
    rows = get_advisor_book_rows(as_of)
    # Exclude unassigned row from "advisor" summary tiles if present
    advisor_rows = [r for r in rows if r.get("advisor_id") is not None]
    return {
        "rows": advisor_rows[:8],
        "all_rows": advisor_rows,
        "unassigned_count": count_unassigned_active_clients(),
        "advisor_count": len(advisor_rows),
        "as_of": as_of,
        "this_month_label": _month_label(as_of),
        "last_month_label": _month_label(_shift_month(as_of, -1)),
    }
