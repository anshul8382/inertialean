"""Aggregates for the Monthly Investment hub dashboard."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from sqlalchemy import func, or_

from extensions import db
from models import Client, MonthlyInvestment, MonthlyInvestmentSchedule, Workflow
from sqlalchemy.orm import joinedload


def _month_bounds(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    last = monthrange(today.year, today.month)[1]
    return date(today.year, today.month, 1), date(today.year, today.month, last)


def get_monthly_investment_hub_context(today: date | None = None) -> dict:
    """
    Returns template context: summary counts, optional drill-down links, month label.
    Counts are scoped to the caller's accessible clients (admin/manager: all).
    """
    from access_control import accessible_client_ids, scope_query_to_accessible_clients

    today = today or date.today()
    month_start, month_end = _month_bounds(today)

    def _scope_wf(q):
        """Scope Workflow rows via monthly_investment.client_id (joins MI when needed)."""
        ids = accessible_client_ids()
        if ids is None:
            return q
        q = q.join(MonthlyInvestment, Workflow.monthly_investment_id == MonthlyInvestment.id)
        if not ids:
            return q.filter(MonthlyInvestment.client_id.in_([-1]))
        return q.filter(MonthlyInvestment.client_id.in_(ids))

    # Investments with investment_date in this calendar month
    inv_month_q = MonthlyInvestment.query.filter(
        MonthlyInvestment.investment_date >= month_start,
        MonthlyInvestment.investment_date <= month_end,
    )
    inv_month_q = scope_query_to_accessible_clients(inv_month_q, MonthlyInvestment.client_id)
    investments_this_month_count = inv_month_q.count()
    investments_this_month_planned = (
        scope_query_to_accessible_clients(
            db.session.query(func.coalesce(func.sum(MonthlyInvestment.planned_amount), 0)).filter(
                MonthlyInvestment.investment_date >= month_start,
                MonthlyInvestment.investment_date <= month_end,
            ),
            MonthlyInvestment.client_id,
        ).scalar()
        or 0
    )

    # Sum workflow actuals for monthly investments dated this month (achieved / executed)
    investments_this_month_achieved = (
        scope_query_to_accessible_clients(
            db.session.query(func.coalesce(func.sum(Workflow.actual_amount), 0))
            .select_from(Workflow)
            .join(MonthlyInvestment, Workflow.monthly_investment_id == MonthlyInvestment.id)
            .filter(
                MonthlyInvestment.investment_date >= month_start,
                MonthlyInvestment.investment_date <= month_end,
            ),
            MonthlyInvestment.client_id,
        ).scalar()
        or 0
    )

    planned_f = float(investments_this_month_planned)
    achieved_f = float(investments_this_month_achieved)
    investments_this_month_variance = planned_f - achieved_f
    investments_this_month_achieved_pct = (
        (achieved_f / planned_f * 100.0) if planned_f > 0 else None
    )

    # Workflows completed this month (by actual_completion_date)
    completed_this_month = _scope_wf(
        Workflow.query.filter(
            Workflow.current_stage == "COMPLETED",
            Workflow.actual_completion_date.isnot(None),
            Workflow.actual_completion_date >= month_start,
            Workflow.actual_completion_date <= month_end,
        )
    ).count()

    # Open (non-completed, non-archived) workflows tied to monthly investments
    open_wf_filter = (
        Workflow.current_stage != "COMPLETED",
        or_(Workflow.is_archived.is_(None), Workflow.is_archived == False),
    )
    pending_workflows_count = _scope_wf(Workflow.query.filter(*open_wf_filter)).count()

    # Active schedules: recurring monthly investment config
    sched_q = MonthlyInvestmentSchedule.query.filter(
        MonthlyInvestmentSchedule.is_active == True,
        MonthlyInvestmentSchedule.start_date <= month_end,
        or_(
            MonthlyInvestmentSchedule.end_date.is_(None),
            MonthlyInvestmentSchedule.end_date >= month_start,
        ),
    )
    sched_q = scope_query_to_accessible_clients(sched_q, MonthlyInvestmentSchedule.client_id)
    active_schedules_count = sched_q.count()
    active_schedules_planned_sum = (
        scope_query_to_accessible_clients(
            db.session.query(func.coalesce(func.sum(MonthlyInvestmentSchedule.planned_amount), 0))
            .select_from(MonthlyInvestmentSchedule)
            .filter(
                MonthlyInvestmentSchedule.is_active == True,
                MonthlyInvestmentSchedule.start_date <= month_end,
                or_(
                    MonthlyInvestmentSchedule.end_date.is_(None),
                    MonthlyInvestmentSchedule.end_date >= month_start,
                ),
            ),
            MonthlyInvestmentSchedule.client_id,
        ).scalar()
        or 0
    )

    # All-time completed workflow count (for "done so far" headline)
    total_completed_workflows = _scope_wf(
        Workflow.query.filter(
            Workflow.current_stage == "COMPLETED",
            or_(Workflow.is_archived.is_(None), Workflow.is_archived == False),
        )
    ).count()

    # --- Inconsistencies (lightweight counts) ---
    # Pending investment row but no workflow row
    no_workflow = scope_query_to_accessible_clients(
        MonthlyInvestment.query.outerjoin(
            Workflow, Workflow.monthly_investment_id == MonthlyInvestment.id
        ).filter(
            MonthlyInvestment.status.ilike("pending"),
            Workflow.id.is_(None),
        ),
        MonthlyInvestment.client_id,
    ).count()

    # Target date passed, still not completed, not archived
    overdue = _scope_wf(
        Workflow.query.filter(
            Workflow.current_stage != "COMPLETED",
            Workflow.target_completion_date < today,
            or_(Workflow.is_archived.is_(None), Workflow.is_archived == False),
        )
    ).count()

    # Investment marked completed but workflow not COMPLETED (or missing)
    stale_status = scope_query_to_accessible_clients(
        MonthlyInvestment.query.outerjoin(
            Workflow, Workflow.monthly_investment_id == MonthlyInvestment.id
        )
        .filter(
            or_(
                MonthlyInvestment.status.ilike("completed"),
                MonthlyInvestment.status == "COMPLETED",
            )
        )
        .filter(
            or_(Workflow.id.is_(None), Workflow.current_stage != "COMPLETED")
        ),
        MonthlyInvestment.client_id,
    ).count()

    # Very old open workflow (>60 days from investment_date)
    old_open = _scope_wf(
        Workflow.query.filter(
            *open_wf_filter,
            Workflow.investment_date < (today - timedelta(days=60)),
        )
    ).count()

    inconsistency_total = no_workflow + overdue + stale_status + old_open

    month_label = today.strftime("%B %Y")

    return {
        "hub_month_label": month_label,
        "hub_month_start": month_start,
        "hub_month_end": month_end,
        "investments_this_month_count": investments_this_month_count,
        "investments_this_month_planned": planned_f,
        "investments_this_month_achieved": achieved_f,
        "investments_this_month_variance": investments_this_month_variance,
        "investments_this_month_achieved_pct": investments_this_month_achieved_pct,
        "completed_workflows_this_month": completed_this_month,
        "pending_workflows_count": pending_workflows_count,
        "active_schedules_count": active_schedules_count,
        "active_schedules_planned_sum": float(active_schedules_planned_sum),
        "total_completed_workflows": total_completed_workflows,
        "inconsistency_total": inconsistency_total,
        "inconsistency_breakdown": {
            "pending_no_workflow": no_workflow,
            "overdue_target": overdue,
            "status_vs_workflow": stale_status,
            "open_over_60d": old_open,
        },
    }


def _month_range_from_yyyy_mm(month_yyyy_mm: str | None) -> tuple[date, date] | None:
    """Parse 'YYYY-MM' into (first_day, last_day) for investment_date filtering."""
    if not month_yyyy_mm:
        return None
    parts = month_yyyy_mm.strip().split("-")
    if len(parts) != 2:
        return None
    try:
        y, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (1900 <= y <= 2100 and 1 <= m <= 12):
        return None
    last = monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last)


def query_monthly_investments_for_list(
    status: str | None = None,
    workflow_status: str | None = None,
    client_id: int | None = None,
    month_yyyy_mm: str | None = None,
):
    """Filtered list for the full monthly investments table."""
    from access_control import scope_query_to_accessible_clients

    q = MonthlyInvestment.query.options(
        joinedload(MonthlyInvestment.client).joinedload(Client.monthly_investment_schedule),
        joinedload(MonthlyInvestment.workflow),
    )
    q = scope_query_to_accessible_clients(q, MonthlyInvestment.client_id)

    bounds = _month_range_from_yyyy_mm(month_yyyy_mm)
    if bounds:
        month_start, month_end = bounds
        q = q.filter(
            MonthlyInvestment.investment_date >= month_start,
            MonthlyInvestment.investment_date <= month_end,
        )

    if client_id:
        q = q.filter(MonthlyInvestment.client_id == client_id)

    if status:
        q = q.filter(MonthlyInvestment.status.ilike(status.strip()))

    wf = (workflow_status or "").strip()
    if wf == "NO_WORKFLOW":
        q = q.outerjoin(Workflow, Workflow.monthly_investment_id == MonthlyInvestment.id).filter(
            Workflow.id.is_(None)
        )
    elif wf:
        q = q.join(Workflow, Workflow.monthly_investment_id == MonthlyInvestment.id).filter(
            Workflow.current_stage == wf
        )

    return q.order_by(MonthlyInvestment.investment_date.desc()).all()


def clients_for_monthly_filters():
    from access_control import get_accessible_clients_ordered
    return get_accessible_clients_ordered()


def _normalize_note_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def apply_monthly_investment_notes_update(
    monthly_investment_id: int,
    investment_notes: str | None,
    schedule_notes: str | None,
) -> None:
    """
    Update per-cycle investment notes on MonthlyInvestment and monthly schedule notes
    on Workflow.schedule_notes when a workflow exists; otherwise on the client's
    active MonthlyInvestmentSchedule row.
    """
    inv = (
        MonthlyInvestment.query.options(joinedload(MonthlyInvestment.workflow))
        .filter_by(id=monthly_investment_id)
        .first()
    )
    if not inv:
        raise ValueError("Monthly investment not found")

    inv.notes = _normalize_note_text(investment_notes)
    sched_text = _normalize_note_text(schedule_notes)

    if inv.workflow is not None:
        inv.workflow.schedule_notes = sched_text
    else:
        sched = MonthlyInvestmentSchedule.query.filter_by(
            client_id=inv.client_id, is_active=True
        ).first()
        if sched is not None:
            sched.notes = sched_text
        elif sched_text is not None:
            raise ValueError(
                "Cannot save monthly schedule notes: this investment has no workflow "
                "and the client has no active monthly investment schedule."
            )
