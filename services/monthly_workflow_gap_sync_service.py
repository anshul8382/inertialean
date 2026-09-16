"""
On-demand reconciliation for monthly investment workflows.

1. For each client with a completed monthly workflow, ensure the next cycle
   exists (same rules as completion-time rollover).
2. For pending monthly investments with no workflow row, create FUNDS workflow
   (same behavior as scheduler.create_workflow_for_investment).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, or_

from extensions import db
from models import MonthlyInvestment, User, Workflow
from services.monthly_workflow_rollover_service import (
    client_has_open_monthly_workflow,
    ensure_next_cycle_after_monthly_workflow_completed,
)

logger = logging.getLogger(__name__)


def run_monthly_workflow_gap_sync(user_id: int) -> dict[str, Any]:
    """
    Idempotent: safe to run repeatedly (rollover and scheduler helpers skip duplicates).

    Returns:
        next_cycles_created: int
        workflows_for_pending_created: int
        clients_checked: int
        pending_without_workflow_seen: int
        errors: list of short strings
    """
    result: dict[str, Any] = {
        "next_cycles_created": 0,
        "workflows_for_pending_created": 0,
        "clients_checked": 0,
        "pending_without_workflow_seen": 0,
        "errors": [],
    }

    client_rows = (
        db.session.query(MonthlyInvestment.client_id)
        .join(Workflow, Workflow.monthly_investment_id == MonthlyInvestment.id)
        .filter(
            Workflow.current_stage == "COMPLETED",
            or_(Workflow.is_archived.is_(None), Workflow.is_archived == False),
        )
        .distinct()
        .all()
    )

    for (client_id,) in client_rows:
        result["clients_checked"] += 1
        latest_inv = (
            MonthlyInvestment.query.join(Workflow)
            .filter(
                MonthlyInvestment.client_id == client_id,
                Workflow.current_stage == "COMPLETED",
                or_(Workflow.is_archived.is_(None), Workflow.is_archived == False),
            )
            .order_by(
                MonthlyInvestment.investment_date.desc(),
                MonthlyInvestment.id.desc(),
            )
            .first()
        )
        if not latest_inv or not latest_inv.workflow:
            continue
        try:
            r = ensure_next_cycle_after_monthly_workflow_completed(
                latest_inv.workflow.id, user_id
            )
            if r.get("created"):
                if r.get("workflow_only"):
                    result["workflows_for_pending_created"] += 1
                else:
                    result["next_cycles_created"] += 1
            if r.get("error"):
                result["errors"].append(
                    f"client {client_id} next cycle: {r['error']}"
                )
        except Exception as e:
            logger.exception("gap_sync next cycle client_id=%s", client_id)
            result["errors"].append(f"client {client_id} next cycle: {e}")

    from scheduler import create_workflow_for_investment

    admin_user = User.query.get(user_id) or User.query.first()
    if not admin_user:
        result["errors"].append("No user found for creating workflows on pending rows.")
        return result

    open_status = (
        func.lower(MonthlyInvestment.status).in_(("pending", "active", "in_progress")),
    )
    pending_no_wf = (
        MonthlyInvestment.query.outerjoin(
            Workflow, Workflow.monthly_investment_id == MonthlyInvestment.id
        )
        .filter(
            *open_status,
            Workflow.id.is_(None),
        )
        .all()
    )

    for inv in pending_no_wf:
        result["pending_without_workflow_seen"] += 1
        if client_has_open_monthly_workflow(inv.client_id):
            continue
        try:
            create_workflow_for_investment(inv, admin_user)
            result["workflows_for_pending_created"] += 1
        except Exception as e:
            logger.exception("gap_sync workflow for investment_id=%s", inv.id)
            result["errors"].append(f"investment {inv.id}: {e}")

    return result
