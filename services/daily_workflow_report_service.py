"""Month windows, filters, and dashboard aggregates for the daily workflow report."""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any

from sqlalchemy import func, or_

from extensions import db
from models import Client, MonthlyInvestment, Workflow


def client_is_active(client) -> bool:
    """Treat missing flag as active for backward compatibility."""
    return getattr(client, "is_active", True) is not False


def filter_workflow_rows(rows: list[tuple], *, active_only: bool) -> list[tuple]:
    """Keep rows whose client matches active_only (True = active, False = inactive)."""
    return [r for r in rows if client_is_active(r[2]) is active_only]


def fetch_inactive_clients_list() -> list[dict[str, Any]]:
    """Inactive clients for the end-of-report section (scoped to caller book)."""
    from access_control import scope_clients_query

    clients = (
        scope_clients_query(Client.query.filter(Client.is_active.is_(False)))
        .order_by(Client.name)
        .all()
    )
    return [
        {
            "name": c.name,
            "phone": c.phone or "—",
        }
        for c in clients
    ]


def active_clients_without_monthly_investment() -> list[dict[str, Any]]:
    """Active clients with no monthly investment records (scoped to caller book)."""
    from access_control import scope_clients_query

    mi_client_ids = db.session.query(MonthlyInvestment.client_id).distinct()
    clients = (
        scope_clients_query(
            Client.query.filter(Client.is_active.is_(True)).filter(~Client.id.in_(mi_client_ids))
        )
        .order_by(Client.name)
        .all()
    )
    return [
        {
            "name": c.name,
            "has_schedule": c.monthly_investment_schedule is not None,
        }
        for c in clients
    ]

# Stages shown in dashboard (non-completed pipeline)
DASHBOARD_STAGES = ("FUNDS", "RECOS", "NOTIFY", "EXEC", "UPDATE")


def month_window(today: date | None = None) -> dict[str, Any]:
    """Current and previous calendar month bounds (inclusive)."""
    today = today or date.today()
    cur_start = date(today.year, today.month, 1)
    cur_end = date(today.year, today.month, monthrange(today.year, today.month)[1])

    if today.month == 1:
        prev_start = date(today.year - 1, 12, 1)
        prev_end = date(today.year - 1, 12, 31)
    else:
        pm = today.month - 1
        prev_start = date(today.year, pm, 1)
        prev_end = date(today.year, pm, monthrange(today.year, pm)[1])

    return {
        "today": today,
        "current_month_start": cur_start,
        "current_month_end": cur_end,
        "prev_month_start": prev_start,
        "prev_month_end": prev_end,
        "report_window_start": prev_start,
        "report_window_end": cur_end,
        "current_month_label": cur_start.strftime("%B %Y"),
        "prev_month_label": prev_start.strftime("%B %Y"),
    }


def _investment_date_col():
    return MonthlyInvestment.investment_date


def base_pending_workflow_query():
    """Non-completed, non-archived workflows joined to investment and client."""
    from access_control import scope_query_to_accessible_clients
    from models import Client

    q = (
        db.session.query(Workflow, MonthlyInvestment, Client)
        .join(MonthlyInvestment, Workflow.monthly_investment_id == MonthlyInvestment.id)
        .join(Client, MonthlyInvestment.client_id == Client.id)
        .filter(
            Workflow.current_stage != "COMPLETED",
            or_(Workflow.is_archived.is_(None), Workflow.is_archived == False),
        )
    )
    return scope_query_to_accessible_clients(q, MonthlyInvestment.client_id)


def pending_workflows_in_report_window(today: date | None = None):
    """Pending workflows for current + previous month only (by investment_date)."""
    w = month_window(today)
    inv = _investment_date_col()
    return (
        base_pending_workflow_query()
        .filter(
            inv >= w["report_window_start"],
            inv <= w["report_window_end"],
        )
        .all()
    )


def bucket_workflows_by_investment_month(
    rows: list[tuple],
    windows: dict[str, Any],
) -> tuple[list[tuple], list[tuple]]:
    """Split (workflow, investment, client) rows into current vs previous month."""
    cur_start = windows["current_month_start"]
    cur_end = windows["current_month_end"]
    prev_start = windows["prev_month_start"]
    prev_end = windows["prev_month_end"]

    current_rows: list[tuple] = []
    prev_rows: list[tuple] = []

    for workflow, investment, client in rows:
        inv_date = investment.investment_date
        if inv_date is None:
            continue
        if cur_start <= inv_date <= cur_end:
            current_rows.append((workflow, investment, client))
        elif prev_start <= inv_date <= prev_end:
            prev_rows.append((workflow, investment, client))

    return current_rows, prev_rows


def planned_investment_for_month(month_start: date, month_end: date) -> float:
    """Sum of planned_amount on monthly investments scheduled in the month (active clients only)."""
    total = (
        db.session.query(func.coalesce(func.sum(MonthlyInvestment.planned_amount), 0))
        .join(Client, MonthlyInvestment.client_id == Client.id)
        .filter(
            MonthlyInvestment.investment_date >= month_start,
            MonthlyInvestment.investment_date <= month_end,
            Client.is_active.is_(True),
        )
        .scalar()
    )
    return float(total or 0)


def pending_by_stage(rows: list[tuple]) -> dict[str, dict[str, float | int]]:
    """Aggregate count and amount per workflow stage."""
    out: dict[str, dict[str, float | int]] = {}
    for workflow, _investment, _client in rows:
        stage = workflow.current_stage or "UNKNOWN"
        if stage not in out:
            out[stage] = {"count": 0, "amount": 0.0}
        out[stage]["count"] += 1
        amt = float(workflow.planned_amount) if workflow.planned_amount else 0.0
        out[stage]["amount"] += amt
    return out


def build_report_dashboard(
    current_month_rows: list[tuple],
    prev_month_rows: list[tuple],
    windows: dict[str, Any],
) -> dict[str, Any]:
    """Top-of-report dashboard: planned month total and pending by stage."""
    cur_by_stage = pending_by_stage(current_month_rows)
    prev_by_stage = pending_by_stage(prev_month_rows)

    def _totals(by_stage: dict[str, dict[str, float | int]]) -> tuple[int, float]:
        count = sum(int(v["count"]) for v in by_stage.values())
        amount = sum(float(v["amount"]) for v in by_stage.values())
        return count, amount

    cur_count, cur_amount = _totals(cur_by_stage)
    prev_count, prev_amount = _totals(prev_by_stage)

    # Ordered stage rows for template (include any extra stages present in data)
    def _ordered_stages(by_stage: dict[str, dict[str, float | int]]) -> list[dict[str, Any]]:
        seen = set(by_stage)
        ordered_keys = [s for s in DASHBOARD_STAGES if s in seen]
        for s in sorted(seen - set(DASHBOARD_STAGES)):
            ordered_keys.append(s)
        rows = []
        for stage in ordered_keys:
            d = by_stage[stage]
            rows.append(
                {
                    "stage": stage,
                    "count": int(d["count"]),
                    "amount": float(d["amount"]),
                }
            )
        return rows

    return {
        "current_month_label": windows["current_month_label"],
        "prev_month_label": windows["prev_month_label"],
        "planned_investment_current_month": planned_investment_for_month(
            windows["current_month_start"],
            windows["current_month_end"],
        ),
        "current_month_pending_by_stage": _ordered_stages(cur_by_stage),
        "prev_month_pending_by_stage": _ordered_stages(prev_by_stage),
        "current_month_pending_count": cur_count,
        "current_month_pending_amount": cur_amount,
        "prev_month_pending_count": prev_count,
        "prev_month_pending_amount": prev_amount,
    }
