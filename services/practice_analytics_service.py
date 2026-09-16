"""
Aggregates firm-wide client and holding metrics for the Practice Analytics dashboard.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from extensions import db
from models import Cashflow, Client, Holding, Lead, MonthlyInvestment, Security, Transaction, Workflow

# Monthly-investment workflow data before this month is excluded from chart and avg/median.
MONTHLY_INVESTMENT_ANALYTICS_START = date(2025, 12, 1)

# Practice AUA projection target (₹100 Cr) for Dec 2027 path analysis.
PRACTICE_AUA_TARGET_INR = 1_000_000_000.0
PRACTICE_AUA_TARGET_END = date(2027, 12, 31)


def _as_float(x: Any) -> float:
    if x is None:
        return 0.0
    if isinstance(x, Decimal):
        return float(x)
    return float(x)


def _active_clients() -> List[Client]:
    """Practice analytics always uses engaged (active) clients only."""
    return (
        Client.query.filter(Client.is_active == True)
        .order_by(Client.id)
        .all()
    )


def _age_bucket(dob: date | None, as_of: date) -> str:
    if not dob:
        return "Unknown"
    try:
        age = int((as_of - dob).days // 365.25)
    except Exception:
        return "Unknown"
    if age < 30:
        return "< 30"
    if age < 40:
        return "30-39"
    if age < 50:
        return "40-49"
    if age < 60:
        return "50-59"
    return "60+"


def _cashflow_row_date(dt: datetime | date | None) -> date | None:
    if dt is None:
        return None
    return dt.date() if isinstance(dt, datetime) else dt


def _first_cashflow_date_by_client(client_ids: List[int]) -> Dict[int, date]:
    if not client_ids:
        return {}
    rows = (
        db.session.query(Cashflow.client_id, func.min(Cashflow.date))
        .filter(Cashflow.client_id.in_(client_ids))
        .group_by(Cashflow.client_id)
        .all()
    )
    out: Dict[int, date] = {}
    for client_id, dt in rows:
        d = _cashflow_row_date(dt)
        if d is not None:
            out[int(client_id)] = d
    return out


def _first_transaction_date_by_client(client_ids: List[int]) -> Dict[int, date]:
    if not client_ids:
        return {}
    rows = (
        db.session.query(Transaction.client_id, func.min(Transaction.transaction_date))
        .filter(Transaction.client_id.in_(client_ids))
        .group_by(Transaction.client_id)
        .all()
    )
    out: Dict[int, date] = {}
    for client_id, dt in rows:
        d = _cashflow_row_date(dt)
        if d is not None:
            out[int(client_id)] = d
    return out


def _lead_by_client_id(client_ids: List[int]) -> Dict[int, Lead]:
    if not client_ids:
        return {}
    rows = Lead.query.filter(Lead.client_id.in_(client_ids)).all()
    return {int(l.client_id): l for l in rows if l.client_id is not None}


def _aging_start_date_fallback(client: Client, leads: Dict[int, Lead]) -> date | None:
    """When no cashflow exists: transaction, joining, record, or lead dates."""
    if client.date_of_joining:
        return client.date_of_joining
    if client.created_at:
        return _cashflow_row_date(client.created_at)
    lead = leads.get(client.id)
    if lead is not None and lead.client_id == client.id:
        if lead.updated_at:
            return lead.updated_at.date()
        if lead.created_at:
            return lead.created_at.date()
    return None


def _aging_start_date_by_client(
    clients: List[Client],
    first_cashflow_by_client: Dict[int, date],
) -> Tuple[Dict[int, date], Dict[str, int]]:
    """
    Relationship start for aging: first cashflow, else fallbacks for clients missing cashflow rows.
    """
    missing = [c for c in clients if c.id not in first_cashflow_by_client]
    txn_by = _first_transaction_date_by_client([c.id for c in missing])
    leads_by = _lead_by_client_id([c.id for c in missing])

    start_by: Dict[int, date] = dict(first_cashflow_by_client)
    used_fallback = 0
    still_unknown = 0

    for c in missing:
        d = txn_by.get(c.id) or _aging_start_date_fallback(c, leads_by)
        if d:
            start_by[c.id] = d
            used_fallback += 1
        else:
            still_unknown += 1

    stats = {
        "with_cashflow": len(first_cashflow_by_client),
        "no_cashflow": len(missing),
        "aging_with_fallback": used_fallback,
        "aging_unknown": still_unknown,
    }
    return start_by, stats


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _last_12_calendar_months(as_of: date) -> List[Tuple[str, date, date, str]]:
    """Oldest-first: (display label, month_start, month_end, YYYY-MM key)."""
    y, m = as_of.year, as_of.month
    months: List[Tuple[str, date, date, str]] = []
    for offset in range(11, -1, -1):
        mm = m - offset
        yy = y
        while mm < 1:
            mm += 12
            yy -= 1
        start = date(yy, mm, 1)
        end = _month_end(start)
        label = start.strftime("%b %Y")
        key = f"{yy:04d}-{mm:02d}"
        months.append((label, start, end, key))
    return months


def _months_on_or_after(
    months: List[Tuple[str, date, date, str]],
    anchor: date,
) -> List[Tuple[str, date, date, str]]:
    """Subset of month tuples whose calendar month starts on or after anchor."""
    return [m for m in months if m[1] >= anchor]


def _first_day_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def _first_month_after(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _calendar_months_range(
    start_month: date,
    end_month: date,
) -> List[Tuple[str, date, date, str]]:
    """Inclusive range of calendar months (first-of-month anchors), oldest first."""
    if start_month > end_month:
        return []
    months: List[Tuple[str, date, date, str]] = []
    y, m = start_month.year, start_month.month
    ey, em = end_month.year, end_month.month
    while (y, m) <= (ey, em):
        start = date(y, m, 1)
        end = _month_end(start)
        label = start.strftime("%b %Y")
        key = f"{y:04d}-{m:02d}"
        months.append((label, start, end, key))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def build_aua_projection_to_target(
    as_of: date,
    current_aua: float,
    avg_monthly_investment: float,
    target_inr: float = PRACTICE_AUA_TARGET_INR,
    target_end: date = PRACTICE_AUA_TARGET_END,
) -> Dict[str, Any]:
    """
    Linear AUA path: current holdings + flat monthly-investment run rate through target_end.

    Does not model market returns — only net planned monthly investment flows.
    """
    projection_start = _first_month_after(as_of)
    target_month = _first_day_of_month(target_end)
    projected_months = _calendar_months_range(projection_start, target_month)
    months_ahead = len(projected_months)

    projected_flow_total = months_ahead * avg_monthly_investment
    projected_aua = current_aua + projected_flow_total
    gap_total = target_inr - projected_aua

    inflow_needed_total = max(0.0, target_inr - current_aua)
    required_monthly = (
        inflow_needed_total / months_ahead if months_ahead > 0 else 0.0
    )
    gap_monthly = required_monthly - avg_monthly_investment

    return {
        "target_inr": target_inr,
        "target_label": target_end.strftime("%b %Y"),
        "target_date": target_end.isoformat(),
        "current_aua": current_aua,
        "avg_monthly_investment": avg_monthly_investment,
        "months_ahead": months_ahead,
        "projected_flow_total": projected_flow_total,
        "projected_aua": projected_aua,
        "gap_total": gap_total,
        "gap_display": max(0.0, gap_total),
        "gap_monthly": gap_monthly,
        "required_monthly_investment": required_monthly,
        "on_track": projected_aua >= target_inr,
    }


def _last_n_monthly_average(values: List[float], n: int = 6) -> float:
    """Mean of the last *n* monthly values (or all values if fewer than *n*)."""
    if not values:
        return 0.0
    tail = values[-n:] if len(values) > n else values
    return sum(tail) / len(tail)


def _monthly_investment_chart_with_target_endpoint(
    actual_by_label: Dict[str, float],
    projection: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Historical monthly investment bars plus one future endpoint (Dec 2027):
    Projected AUM (dark) and Gap to target (light), dotted to mark projection.
    """
    labels = list(actual_by_label.keys())
    future_label = f"{projection.get('target_label', 'Dec 2027')} ···"
    labels.append(future_label)

    completed: List[float | None] = [
        actual_by_label[k] for k in actual_by_label.keys()
    ]
    completed.append(None)

    gap_val = projection.get("gap_display", max(0.0, projection.get("gap_total", 0.0)))
    n_hist = len(actual_by_label)
    projected_aum: List[float | None] = [None] * n_hist + [projection.get("projected_aua")]
    gap: List[float | None] = [None] * n_hist + ([gap_val] if gap_val > 0 else [None])

    return {
        "labels": labels,
        "completed": completed,
        "projected_aum": projected_aum,
        "gap": gap,
        "future_index": len(labels) - 1,
    }


def _tenure_bucket(
    client: Client,
    as_of: date,
    aging_start_by_client: Dict[int, date],
) -> str:
    start = aging_start_by_client.get(client.id)
    if not start:
        return "Unknown"
    try:
        years = (as_of - start).days / 365.25
    except Exception:
        return "Unknown"
    if years < 1:
        return "0-1 years"
    if years < 3:
        return "1-3 years"
    if years < 5:
        return "3-5 years"
    return "5+ years"


def _norm_label(val: Any, default: str = "Unknown") -> str:
    if val is None:
        return default
    s = str(val).strip()
    return s if s else default


def _aggregate_holdings(
    client_ids: List[int],
) -> Tuple[Dict[int, float], Dict[int, float], Dict[str, float]]:
    """Per-client current value, cost basis, and practice-wide value by asset class name."""
    current_by: Dict[int, float] = defaultdict(float)
    cost_by: Dict[int, float] = defaultdict(float)
    asset_class_values: Dict[str, float] = defaultdict(float)
    if not client_ids:
        return dict(current_by), dict(cost_by), dict(asset_class_values)

    holdings = (
        Holding.query.filter(Holding.client_id.in_(client_ids))
        .options(joinedload(Holding.security).joinedload(Security.asset_class))
        .all()
    )
    from services.holding_advisory_scope_service import SCOPE_PRACTICE_AUA, get_excluded_map

    excluded_map = get_excluded_map(client_ids, SCOPE_PRACTICE_AUA)
    for h in holdings:
        sec = h.security
        if not sec:
            continue
        if h.security_id in excluded_map.get(h.client_id, set()):
            continue
        qty = _as_float(h.quantity)
        ap = _as_float(h.average_price)
        cost_by[h.client_id] += qty * ap
        price = _as_float(sec.current_price)
        if price <= 0:
            continue
        val = qty * price
        current_by[h.client_id] += val
        ac = sec.asset_class
        ac_name = (ac.name if ac is not None else None) or "Unknown"
        asset_class_values[_norm_label(ac_name, "Unknown")] += val
    return dict(current_by), dict(cost_by), dict(asset_class_values)


def _new_clients_by_month(
    clients: List[Client],
    months: List[Tuple[str, date, date, str]],
    first_cashflow_by_client: Dict[int, date],
) -> Dict[str, int]:
    counts = {label: 0 for label, _, _, _ in months}
    for c in clients:
        added = first_cashflow_by_client.get(c.id)
        if not added:
            continue
        for label, start, end, _ in months:
            if start <= added <= end:
                counts[label] += 1
                break
    return counts


def _new_clients_aua_12m_summary(
    clients: List[Client],
    first_cashflow_by_client: Dict[int, date],
    current_by_client: Dict[int, float],
    window_start: date,
    as_of: date,
) -> Dict[str, float | int]:
    """Clients whose first cashflow is in the rolling 12-month window; sum and avg current AUA (holdings)."""
    new_in_window: List[Client] = []
    for c in clients:
        added = first_cashflow_by_client.get(c.id)
        if added and window_start <= added <= as_of:
            new_in_window.append(c)
    count = len(new_in_window)
    total_aua = sum(current_by_client.get(c.id, 0.0) for c in new_in_window)
    avg_aua = total_aua / count if count else 0.0
    return {
        "count": count,
        "total_aua": total_aua,
        "avg_aua": avg_aua,
    }


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _completed_investment_by_month(
    client_ids: List[int],
    months: List[Tuple[str, date, date, str]],
    window_start: date,
    as_of: date,
) -> Dict[str, float]:
    """
    Sum planned monthly-investment amounts for completed workflows, bucketed by
    ``MonthlyInvestment.investment_date`` month.

    Aligns with the daily monthly investments report: active (non-archived)
    COMPLETED workflows only; uses ``MonthlyInvestment.planned_amount``.
    """
    labels = [label for label, _, _, _ in months]
    if not client_ids:
        return {label: 0.0 for label in labels}

    window_end = months[-1][2] if months else as_of
    not_archived = or_(Workflow.is_archived.is_(None), Workflow.is_archived == False)
    ym_expr = func.date_format(MonthlyInvestment.investment_date, "%Y-%m")
    rows = (
        db.session.query(ym_expr, func.coalesce(func.sum(MonthlyInvestment.planned_amount), 0))
        .select_from(Workflow)
        .join(MonthlyInvestment, Workflow.monthly_investment_id == MonthlyInvestment.id)
        .filter(
            MonthlyInvestment.client_id.in_(client_ids),
            Workflow.current_stage == "COMPLETED",
            not_archived,
            MonthlyInvestment.investment_date.isnot(None),
            MonthlyInvestment.investment_date >= window_start,
            MonthlyInvestment.investment_date <= window_end,
        )
        .group_by(ym_expr)
        .all()
    )
    by_key = {str(r[0]): _as_float(r[1]) for r in rows if r[0] is not None}
    return {label: by_key.get(key, 0.0) for label, _, _, key in months}


def build_practice_analytics_context() -> Dict[str, Any]:
    as_of = date.today()
    clients = _active_clients()
    total_clients = len(clients)
    client_ids = [c.id for c in clients]

    current_by, cost_by, asset_class_values = _aggregate_holdings(client_ids)
    first_cashflow_by_client = _first_cashflow_date_by_client(client_ids)
    aging_start_by_client, aging_date_stats = _aging_start_date_by_client(
        clients, first_cashflow_by_client
    )

    total_current_value = sum(current_by.values())
    total_invested_all = sum(cost_by.values())
    total_wealth_generated = total_current_value - total_invested_all

    age_distribution: Dict[str, int] = defaultdict(int)
    industry_distribution: Dict[str, int] = defaultdict(int)
    designation_distribution: Dict[str, int] = defaultdict(int)
    engagement_distribution: Dict[str, int] = defaultdict(int)
    tenure_counts: Dict[str, int] = defaultdict(int)
    inherited_vs_built: Dict[str, int] = defaultdict(int)

    for c in clients:
        age_distribution[_age_bucket(c.date_of_birth, as_of)] += 1
        industry_distribution[_norm_label(c.industry)] += 1
        designation_distribution[_norm_label(c.designation)] += 1
        engagement_distribution[_norm_label(c.type_of_engagement)] += 1
        tenure_counts[_tenure_bucket(c, as_of, aging_start_by_client)] += 1
        if c.portfolio_inherited:
            inherited_vs_built["Acquired"] += 1
        else:
            inherited_vs_built["Built"] += 1

    n = total_clients or 1
    aging_metrics = {k: round(100.0 * v / n, 2) for k, v in tenure_counts.items()}

    last_12_months = _last_12_calendar_months(as_of)
    window_start = last_12_months[0][1]
    new_clients_by_month = _new_clients_by_month(
        clients, last_12_months, first_cashflow_by_client
    )
    new_clients_aua_12m = _new_clients_aua_12m_summary(
        clients, first_cashflow_by_client, current_by, window_start, as_of
    )
    new_clients_12m_total = int(new_clients_aua_12m["count"])
    new_clients_avg_per_month = (
        new_clients_12m_total / len(last_12_months) if last_12_months else 0.0
    )
    monthly_investment_months = _months_on_or_after(
        last_12_months, MONTHLY_INVESTMENT_ANALYTICS_START
    )
    mi_window_start = (
        monthly_investment_months[0][1] if monthly_investment_months else window_start
    )
    monthly_investment_by_month = _completed_investment_by_month(
        client_ids, monthly_investment_months, mi_window_start, as_of
    )
    monthly_inv_monthly_values = list(monthly_investment_by_month.values())
    monthly_investment_12m_avg = (
        sum(monthly_inv_monthly_values) / len(monthly_inv_monthly_values)
        if monthly_inv_monthly_values
        else 0.0
    )
    monthly_investment_12m_median = _median(monthly_inv_monthly_values)

    projection_run_rate_6m = _last_n_monthly_average(monthly_inv_monthly_values, 6)
    monthly_investment_projection = build_aua_projection_to_target(
        as_of,
        total_current_value,
        projection_run_rate_6m,
    )
    monthly_investment_projection["projection_run_rate_label"] = "Last 6 months"
    monthly_investment_chart = _monthly_investment_chart_with_target_endpoint(
        monthly_investment_by_month,
        monthly_investment_projection,
    )

    return {
        "total_clients": total_clients,
        "total_current_value": total_current_value,
        "total_invested_all": total_invested_all,
        "total_wealth_generated": total_wealth_generated,
        "age_distribution": dict(age_distribution),
        "industry_distribution": dict(industry_distribution),
        "designation_distribution": dict(designation_distribution),
        "engagement_distribution": dict(engagement_distribution),
        "asset_class_distribution": dict(asset_class_values),
        "aging_metrics": aging_metrics,
        "inherited_vs_built": dict(inherited_vs_built),
        "new_clients_by_month": new_clients_by_month,
        "new_clients_12m_count": new_clients_12m_total,
        "new_clients_avg_per_month": new_clients_avg_per_month,
        "new_clients_12m_aua_total": new_clients_aua_12m["total_aua"],
        "new_clients_12m_aua_avg": new_clients_aua_12m["avg_aua"],
        "monthly_investment_by_month": monthly_investment_by_month,
        "monthly_investment_12m_avg": monthly_investment_12m_avg,
        "monthly_investment_12m_median": monthly_investment_12m_median,
        "monthly_investment_period_label": "Dec 2025 onwards",
        "monthly_investment_projection": monthly_investment_projection,
        "monthly_investment_chart": monthly_investment_chart,
        "clients": clients,
        "current_by_client": current_by,
        "cost_by_client": cost_by,
        "first_cashflow_by_client": first_cashflow_by_client,
        "aging_date_stats": aging_date_stats,
        "as_of": as_of,
    }


def build_practice_analytics_workbook_bytes() -> Tuple[bytes, str]:
    """Build Excel export; client name and email are always partially masked."""
    from io import BytesIO

    from openpyxl import Workbook

    from services.pii_masking import mask_client_name, mask_email

    ctx = build_practice_analytics_context()
    wb = Workbook()
    ws0 = wb.active
    ws0.title = "Summary"
    ws0.append(["Practice Analytics Export"])
    ws0.append(["Generated (UTC)", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")])
    ws0.append(["As-of (local date)", str(ctx["as_of"])])
    ws0.append(["Client scope", "Active clients only"])
    ws0.append(["Clients sheet PII", "Name and email partially masked"])
    ws0.append([])
    ws0.append(["Total clients", ctx["total_clients"]])
    ws0.append(["Total current value", ctx["total_current_value"]])
    ws0.append(["Total invested (cost basis)", ctx["total_invested_all"]])
    ws0.append(["Wealth generated", ctx["total_wealth_generated"]])
    ws0.append([])
    ws0.append(["New clients (first cashflow, last 12 months)", ctx["new_clients_12m_count"]])
    ws0.append(["Avg new clients per month", ctx["new_clients_avg_per_month"]])
    ads = ctx.get("aging_date_stats") or {}
    ws0.append(["Clients with cashflow (aging)", ads.get("with_cashflow", 0)])
    ws0.append(["Clients without cashflow rows", ads.get("no_cashflow", 0)])
    ws0.append(["Aging used fallback date", ads.get("aging_with_fallback", 0)])
    ws0.append(["Aging Unknown (no date source)", ads.get("aging_unknown", 0)])
    ws0.append([])
    mi_period = ctx.get("monthly_investment_period_label") or "Dec 2025 onwards"
    ws0.append(
        [f"Avg monthly investment ({mi_period})", ctx["monthly_investment_12m_avg"]]
    )
    ws0.append(
        [f"Median monthly investment ({mi_period})", ctx["monthly_investment_12m_median"]]
    )
    proj = ctx.get("monthly_investment_projection") or {}
    if proj:
        ws0.append([])
        ws0.append([f"AUA projection to {proj.get('target_label', 'Dec 2027')}", ""])
        ws0.append(["Current AUA", proj.get("current_aua", 0)])
        ws0.append(
            ["Avg monthly investment (projection run rate, last 6 mo)",
             proj.get("avg_monthly_investment", 0)]
        )
        ws0.append(["Months to target", proj.get("months_ahead", 0)])
        ws0.append(["Projected AUA at target", proj.get("projected_aua", 0)])
        ws0.append(["Target AUA", proj.get("target_inr", 0)])
        ws0.append(["Gap (total)", proj.get("gap_total", 0)])
        ws0.append(["Gap (monthly vs run rate)", proj.get("gap_monthly", 0)])
        ws0.append(
            ["Required avg monthly investment", proj.get("required_monthly_investment", 0)]
        )
    ws0.append(["Total current AUA (those clients)", ctx["new_clients_12m_aua_total"]])
    ws0.append(["Avg current AUA (those clients)", ctx["new_clients_12m_aua_avg"]])

    ws1 = wb.create_sheet("Clients")
    headers = [
        "Client ID",
        "Name",
        "Email",
        "Age band",
        "Industry",
        "Designation",
        "Engagement",
        "Portfolio inherited",
        "Tenure band (first cashflow)",
        "Current value",
        "Cost basis",
    ]
    ws1.append(headers)
    current_by = ctx["current_by_client"]
    cost_by = ctx["cost_by_client"]
    aging_start_by_client, _ = _aging_start_date_by_client(
        ctx["clients"], ctx["first_cashflow_by_client"]
    )
    as_of = ctx["as_of"]
    for c in ctx["clients"]:
        ws1.append(
            [
                c.id,
                mask_client_name(c.name),
                mask_email(c.email),
                _age_bucket(c.date_of_birth, as_of),
                _norm_label(c.industry),
                _norm_label(c.designation),
                _norm_label(c.type_of_engagement),
                bool(c.portfolio_inherited),
                _tenure_bucket(c, as_of, aging_start_by_client),
                current_by.get(c.id, 0.0),
                cost_by.get(c.id, 0.0),
            ]
        )

    ws2 = wb.create_sheet("Last 12 Months")
    ws2.append(
        [
            "Month",
            "New clients (first cashflow)",
            "Monthly investment (completed, non-archived, by investment month, planned amount)",
        ]
    )
    as_of = ctx["as_of"]
    last_12 = _last_12_calendar_months(as_of)
    mi_months = _months_on_or_after(last_12, MONTHLY_INVESTMENT_ANALYTICS_START)
    for label, _, _, _ in last_12:
        mi_val = ctx["monthly_investment_by_month"].get(label)
        ws2.append(
            [
                label,
                ctx["new_clients_by_month"].get(label, 0),
                mi_val if mi_val is not None else "",
            ]
        )
    if mi_months and mi_months[0][1] > last_12[0][1]:
        ws2.append([])
        ws2.append(
            [
                "Note",
                "",
                f"Monthly investment column from {mi_period} only (earlier months omitted)",
            ]
        )

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"practice_analytics_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return buf.getvalue(), fname
