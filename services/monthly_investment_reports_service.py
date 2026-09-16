"""Data builders for monthly-investment HTML reports (stage aging, days since recos)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from extensions import db
from models import Client, MonthlyInvestment, Workflow, WorkflowAction


def _naive_utc(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.replace(tzinfo=None)
    return dt


STAGE_INFO: dict[str, dict[str, str]] = {
    "FUNDS": {
        "description": "Awaiting funds / ICR",
        "color": "#ffc107",
        "icon": "fa-wallet",
    },
    "RECOS": {
        "description": "Recommendations",
        "color": "#17a2b8",
        "icon": "fa-chart-line",
    },
    "NOTIFY": {
        "description": "Client notification",
        "color": "#6f42c1",
        "icon": "fa-bell",
    },
    "EXEC": {
        "description": "Execution",
        "color": "#fd7e14",
        "icon": "fa-bolt",
    },
    "UPDATE": {
        "description": "Portfolio update",
        "color": "#20c997",
        "icon": "fa-sync",
    },
    "COMPLETED": {
        "description": "Completed",
        "color": "#28a745",
        "icon": "fa-check",
    },
    "NO_WORKFLOW": {
        "description": "No workflow",
        "color": "#6c757d",
        "icon": "fa-minus-circle",
    },
    "NO_MONTHLY_INVESTMENT": {
        "description": "No monthly investment row",
        "color": "#adb5bd",
        "icon": "fa-inbox",
    },
}


def _normalize_stage(stage: str | None) -> str:
    if not stage:
        return "FUNDS"
    if stage in ("ICR", "INVESTMENT/CHANGES/REDEMPTION"):
        return "FUNDS"
    return stage


def build_stage_aging_report_context() -> dict[str, Any]:
    """Context for templates/reports/monthly_investments_stage_aging.html"""
    from access_control import scope_query_to_accessible_clients

    now = _naive_utc(datetime.utcnow())
    report_date = now.date() if now else None

    q = (
        db.session.query(Workflow, MonthlyInvestment, Client)
        .join(MonthlyInvestment, Workflow.monthly_investment_id == MonthlyInvestment.id)
        .join(Client, MonthlyInvestment.client_id == Client.id)
        .filter(
            Workflow.current_stage != "COMPLETED",
            or_(Workflow.is_archived.is_(None), Workflow.is_archived == False),
        )
        .options(joinedload(MonthlyInvestment.client))
    )
    q = scope_query_to_accessible_clients(q, MonthlyInvestment.client_id)

    report_data: list[dict[str, Any]] = []
    for wf, mi, client in q.all():
        stage = _normalize_stage(wf.current_stage)
        ref = _naive_utc(wf.updated_at) or _naive_utc(wf.created_at) or now
        days_in_stage = max(0, (now - ref).days) if ref else 0
        report_data.append(
            {
                "client_name": client.name or "",
                "current_stage": stage,
                "days_in_stage": days_in_stage,
                "stage_entry_date": ref.strftime("%Y-%m-%d %H:%M") if ref else "N/A",
                "investment_date": mi.investment_date.strftime("%Y-%m-%d")
                if mi.investment_date
                else "N/A",
                "planned_amount": float(mi.planned_amount or 0),
                "actual_amount": float(wf.actual_amount or 0) if wf.actual_amount is not None else 0.0,
                "investment_id": mi.id,
            }
        )

    grouped_data: dict[str, list] = defaultdict(list)
    for row in report_data:
        grouped_data[row["current_stage"]].append(row)
    for stage in grouped_data:
        grouped_data[stage].sort(key=lambda x: -x["days_in_stage"])

    order = ["FUNDS", "RECOS", "NOTIFY", "EXEC", "UPDATE"]
    sorted_stages = [s for s in order if s in grouped_data]
    sorted_stages.extend(sorted(s for s in grouped_data if s not in order))

    unique_stages = len(grouped_data)
    if report_data:
        avg_days = sum(r["days_in_stage"] for r in report_data) / len(report_data)
        max_days = max(r["days_in_stage"] for r in report_data)
    else:
        avg_days = 0.0
        max_days = 0

    return {
        "report_date": report_date,
        "report_data": report_data,
        "grouped_data": dict(grouped_data),
        "sorted_stages": sorted_stages,
        "stage_info": STAGE_INFO,
        "unique_stages": unique_stages,
        "avg_days": avg_days,
        "max_days": max_days,
    }


def build_days_from_recos_report_context() -> dict[str, Any]:
    """Context for templates/reports/monthly_investments_days_from_recos.html"""
    now = _naive_utc(datetime.utcnow())
    today = now.date() if now else datetime.utcnow().date()
    report_date = today

    from access_control import get_accessible_clients_ordered
    clients = get_accessible_clients_ordered()
    report_data: list[dict[str, Any]] = []

    for client in clients:
        mi = (
            MonthlyInvestment.query.filter_by(client_id=client.id)
            .order_by(MonthlyInvestment.investment_date.desc())
            .first()
        )
        wf = mi.workflow if mi else None

        last_reco = (
            db.session.query(func.max(WorkflowAction.action_date))
            .select_from(WorkflowAction)
            .join(Workflow, WorkflowAction.workflow_id == Workflow.id)
            .join(MonthlyInvestment, Workflow.monthly_investment_id == MonthlyInvestment.id)
            .filter(
                MonthlyInvestment.client_id == client.id,
                WorkflowAction.action_type == "RECOS_GENERATED",
            )
            .scalar()
        )
        last_reco = _naive_utc(last_reco)

        if last_reco:
            days_since_reco = (today - last_reco.date()).days
            last_execution_date = last_reco.strftime("%Y-%m-%d %H:%M")
        else:
            days_since_reco = None
            last_execution_date = "—"

        if not mi:
            current_stage = "NO_MONTHLY_INVESTMENT"
        elif not wf:
            current_stage = "NO_WORKFLOW"
        else:
            current_stage = _normalize_stage(wf.current_stage)

        report_data.append(
            {
                "client_id": client.id,
                "client_name": client.name or "",
                "days_since_reco": days_since_reco,
                "last_execution_date": last_execution_date,
                "has_monthly_investment": mi is not None,
                "current_stage": current_stage,
                "planned_amount": float(mi.planned_amount or 0) if mi else 0.0,
                "investment_id": mi.id if mi else None,
            }
        )

    report_data.sort(
        key=lambda r: (
            r["days_since_reco"] is None,
            -(r["days_since_reco"] or 0),
        )
    )

    clients_with_mi = sum(1 for r in report_data if r["has_monthly_investment"])
    never_executed_count = sum(1 for r in report_data if r["days_since_reco"] is None)
    old_recos_count = sum(1 for r in report_data if (r["days_since_reco"] or 0) >= 30)
    with_days = [r["days_since_reco"] for r in report_data if r["days_since_reco"] is not None]
    avg_days = sum(with_days) / len(with_days) if with_days else None

    return {
        "report_date": report_date,
        "report_data": report_data,
        "total_clients": len(clients),
        "clients_with_mi": clients_with_mi,
        "never_executed_count": never_executed_count,
        "old_recos_count": old_recos_count,
        "avg_days": avg_days,
    }
