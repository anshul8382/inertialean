"""
When a monthly investment workflow reaches COMPLETED, create the next cycle
(MonthlyInvestment + Workflow) from the client's active MonthlyInvestmentSchedule
when possible, otherwise using the same cadence as legacy auto-creation (next month).
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import or_

from extensions import db
from models import MonthlyInvestment, MonthlyInvestmentSchedule, User, Workflow
from services.investment_cycle import is_due_change, validate_amount_for_schedule

logger = logging.getLogger(__name__)


def _scheduled_day_in_month(schedule: MonthlyInvestmentSchedule, y: int, m: int) -> date:
    last_day = calendar.monthrange(y, m)[1]
    dom = schedule.day_of_month or 1
    safe = min(dom, last_day)
    return date(y, m, safe)


def _next_investment_date_from_schedule(
    schedule: MonthlyInvestmentSchedule,
    after_investment_date: date,
    today: date,
) -> Optional[date]:
    """
    First schedule due date strictly after ``after_investment_date``,
    then bumped forward if before ``today`` (late completion).
    """
    if not schedule or not schedule.is_active:
        return None
    if (schedule.change_frequency_months or 0) < 1:
        return None
    if schedule.end_date and schedule.end_date < after_investment_date:
        return None

    y, m = after_investment_date.year, after_investment_date.month
    for _ in range(48):
        cand = _scheduled_day_in_month(schedule, y, m)
        if cand <= after_investment_date:
            pass
        elif schedule.start_date and cand < schedule.start_date:
            pass
        elif schedule.end_date and cand > schedule.end_date:
            return None
        elif is_due_change(schedule, cand):
            # Do not target the same calendar month as the closed cycle (e.g. close Apr 5,
            # schedule day 15 — next cycle must be May, not another April date).
            if (cand.year, cand.month) <= (after_investment_date.year, after_investment_date.month):
                pass
            else:
                return max(cand, today)
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return None


def _fallback_next_investment_date(after_investment_date: date, today: date) -> date:
    """First day of month following ``after_investment_date``'s month, or today if later."""
    anchor = after_investment_date.replace(day=1)
    next_first = anchor + timedelta(days=32)
    next_first = next_first.replace(day=1)
    return max(next_first, today)


def client_has_open_monthly_workflow(
    client_id: int, exclude_workflow_id: int | None = None
) -> bool:
    """True if the client has any non-archived monthly workflow not in COMPLETED."""
    q = Workflow.query.join(MonthlyInvestment).filter(
        MonthlyInvestment.client_id == client_id,
        Workflow.current_stage != "COMPLETED",
        or_(Workflow.is_archived.is_(None), Workflow.is_archived == False),
    )
    if exclude_workflow_id is not None:
        q = q.filter(Workflow.id != exclude_workflow_id)
    return q.first() is not None


def _client_has_blocking_open_workflow(client_id: int, exclude_workflow_id: int) -> bool:
    return client_has_open_monthly_workflow(client_id, exclude_workflow_id)


def _calendar_month_bounds(d: date) -> tuple[date, date]:
    month_start = d.replace(day=1)
    if month_start.month == 12:
        month_end = date(month_start.year + 1, 1, 1)
    else:
        month_end = date(month_start.year, month_start.month + 1, 1)
    return month_start, month_end


def _attach_workflow_to_first_orphan_in_month(
    client_id: int, month_anchor_date: date, user_id: int
) -> dict[str, Any]:
    """
    If a non-cancelled monthly investment exists in that calendar month with no workflow,
    create FUNDS workflow (same as scheduler). Returns ``created`` True when linked.
    """
    out: dict[str, Any] = {}
    month_start, month_end = _calendar_month_bounds(month_anchor_date)
    orphans = (
        MonthlyInvestment.query.outerjoin(
            Workflow, Workflow.monthly_investment_id == MonthlyInvestment.id
        )
        .filter(
            MonthlyInvestment.client_id == client_id,
            MonthlyInvestment.investment_date >= month_start,
            MonthlyInvestment.investment_date < month_end,
            Workflow.id.is_(None),
        )
        .order_by(MonthlyInvestment.id.asc())
        .all()
    )
    admin_user = User.query.get(user_id) or User.query.first()
    if not admin_user:
        out["error"] = "No user found to attach workflow."
        return out
    for inv in orphans:
        st = (inv.status or "").lower()
        if "cancel" in st:
            continue
        from scheduler import create_workflow_for_investment

        create_workflow_for_investment(inv, admin_user)
        out["created"] = True
        out["workflow_only"] = True
        out["monthly_investment_id"] = inv.id
        out["message"] = (
            f"Created FUNDS workflow on existing investment #{inv.id} "
            f"({month_start.isoformat()}–exclusive {month_end.isoformat()})."
        )
        logger.info(
            "rollover: attached workflow to orphan investment %s client %s",
            inv.id,
            client_id,
        )
        return out
    return out


def _month_has_non_cancelled_investment(client_id: int, investment_date: date) -> bool:
    """True if any non-cancelled monthly investment row exists in that calendar month."""
    month_start, month_end = _calendar_month_bounds(investment_date)
    rows = MonthlyInvestment.query.filter(
        MonthlyInvestment.client_id == client_id,
        MonthlyInvestment.investment_date >= month_start,
        MonthlyInvestment.investment_date < month_end,
    ).all()
    for inv in rows:
        if "cancel" not in (inv.status or "").lower():
            return True
    return False


def ensure_next_cycle_after_monthly_workflow_completed(
    workflow_id: int, user_id: int
) -> dict[str, Any]:
    """
    Idempotent: safe to call after COMPLETED; skips if next cycle already exists or
    another open workflow exists for the client.
    """
    result: dict[str, Any] = {"created": False, "monthly_investment_id": None, "workflow_id": None}

    wf = Workflow.query.get(workflow_id)
    if not wf or wf.current_stage != "COMPLETED":
        return result
    inv = wf.monthly_investment
    if not inv or not inv.client_id:
        logger.warning("rollover: workflow %s missing investment or client_id", workflow_id)
        return result

    if not inv.portfolio_id:
        from services.monthly_investment_portfolio_service import (
            ensure_monthly_investment_portfolio_id,
        )

        try:
            if ensure_monthly_investment_portfolio_id(inv, user_id):
                db.session.flush()
        except Exception as e:
            logger.exception("rollover: could not resolve portfolio for workflow %s", workflow_id)
            result["error"] = f"Could not resolve portfolio for this investment: {e}"
            return result

    client_id = inv.client_id
    if _client_has_blocking_open_workflow(client_id, workflow_id):
        logger.info(
            "rollover: skip client %s — another open monthly workflow exists", client_id
        )
        return result

    today = datetime.utcnow().date()
    schedule = MonthlyInvestmentSchedule.query.filter_by(
        client_id=client_id, is_active=True
    ).first()

    investment_date: Optional[date] = None
    planned_amount: Optional[float] = None
    sched_for_validate: Optional[MonthlyInvestmentSchedule] = schedule

    if schedule:
        investment_date = _next_investment_date_from_schedule(
            schedule, inv.investment_date, today
        )
        if investment_date is not None:
            planned_amount = float(schedule.planned_amount)

    if investment_date is None:
        investment_date = _fallback_next_investment_date(inv.investment_date, today)
        planned_amount = float(inv.planned_amount)
        sched_for_validate = schedule

    if not validate_amount_for_schedule(planned_amount, sched_for_validate):
        logger.warning(
            "rollover: skip client %s — amount %s not allowed by schedule",
            client_id,
            planned_amount,
        )
        result["error"] = "Next cycle was not created: amount is not allowed for this schedule."
        return result

    attached = _attach_workflow_to_first_orphan_in_month(
        client_id, investment_date, user_id
    )
    if attached.get("error"):
        result["error"] = attached["error"]
        return result
    if attached.get("created"):
        result.update(attached)
        return result

    if _month_has_non_cancelled_investment(client_id, investment_date):
        logger.info(
            "rollover: skip client %s — calendar month of %s already has a non-cancelled investment",
            client_id,
            investment_date,
        )
        return result

    notes_schedule = schedule.notes if schedule and getattr(schedule, "notes", None) else None
    new_inv = MonthlyInvestment(
        client_id=client_id,
        portfolio_id=inv.portfolio_id,
        planned_amount=planned_amount,
        investment_date=investment_date,
        status="pending",
        created_by=user_id,
    )
    db.session.add(new_inv)
    db.session.flush()

    new_wf = Workflow(
        monthly_investment_id=new_inv.id,
        current_stage="FUNDS",
        planned_amount=planned_amount,
        investment_date=investment_date,
        target_completion_date=investment_date + timedelta(days=30),
        created_by=user_id,
        schedule_notes=notes_schedule,
        notes=f"Auto-created after completion of workflow {workflow_id}",
    )
    db.session.add(new_wf)
    db.session.commit()

    result["created"] = True
    result["monthly_investment_id"] = new_inv.id
    result["workflow_id"] = new_wf.id
    result["message"] = (
        f"Next cycle created: investment #{new_inv.id} dated {investment_date.isoformat()}."
    )
    logger.info("rollover: created investment %s workflow %s for client %s", new_inv.id, new_wf.id, client_id)
    return result
