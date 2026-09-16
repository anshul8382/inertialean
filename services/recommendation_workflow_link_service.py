"""
Link RecommendationSession rows to monthly-investment Workflow records.

Adhoc sessions often had no workflow_id; stage updates (RECOS, NOTIFY, UPDATE) were skipped.
This module resolves an existing workflow or creates MonthlyInvestment + Workflow when appropriate.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from extensions import db
from models import (
    Client,
    MonthlyInvestment,
    MonthlyInvestmentSchedule,
    Portfolio,
    Workflow,
)

if TYPE_CHECKING:
    from models import RecommendationSession

logger = logging.getLogger(__name__)

WORKFLOW_ID_PATTERN = re.compile(r"WORKFLOW_ID:(\d+)", re.MULTILINE)


def _session_is_adhoc(recommendation_session) -> bool:
    st = (getattr(recommendation_session, "session_type", None) or "").strip().lower()
    return st == "adhoc"


def extract_workflow_id_from_notes(notes: Optional[str]) -> Optional[int]:
    if not notes:
        return None
    m = WORKFLOW_ID_PATTERN.search(notes)
    if not m:
        return None
    return int(m.group(1))


def notes_contain_workflow_id(notes: Optional[str]) -> bool:
    return bool(notes and WORKFLOW_ID_PATTERN.search(notes))


def _append_workflow_id_to_session_notes(recommendation_session, workflow_id: int) -> bool:
    """Append WORKFLOW_ID to session notes if missing. Returns True if notes were modified."""
    if not recommendation_session or notes_contain_workflow_id(recommendation_session.notes):
        return False
    base = (recommendation_session.notes or "").rstrip()
    recommendation_session.notes = f"{base}\nWORKFLOW_ID:{workflow_id}" if base else f"WORKFLOW_ID:{workflow_id}"
    return True


def resolve_workflow_for_recommendation_session(recommendation_session, client_id: int) -> Optional[Workflow]:
    """
    Resolve workflow without creating:
    1. WORKFLOW_ID in session notes (non-archived)
    2. Latest PENDING/ACTIVE MonthlyInvestment with non-archived workflow
    """
    if recommendation_session and recommendation_session.notes:
        wid = extract_workflow_id_from_notes(recommendation_session.notes)
        if wid is not None:
            wf = Workflow.query.get(wid)
            if wf and not wf.is_archived:
                logger.info("Resolved workflow %s from session notes (client_id=%s)", wid, client_id)
                return wf

    active_investment = (
        MonthlyInvestment.query.filter(
            MonthlyInvestment.client_id == client_id,
            MonthlyInvestment.status.in_(["PENDING", "ACTIVE", "pending", "active"]),
        )
        .order_by(MonthlyInvestment.id.desc())
        .first()
    )

    if active_investment and active_investment.workflow and not active_investment.workflow.is_archived:
        logger.info(
            "Resolved workflow %s from MonthlyInvestment %s (client_id=%s)",
            active_investment.workflow.id,
            active_investment.id,
            client_id,
        )
        return active_investment.workflow

    logger.info("No workflow resolved for client_id=%s", client_id)
    return None


def _get_or_create_portfolio(client: Client, user_id: int) -> Portfolio:
    portfolio = Portfolio.query.filter_by(client_id=client.id, status="active").first()
    if not portfolio and getattr(client, "portfolios", None):
        portfolio = client.portfolios[0]
    if not portfolio:
        portfolio = Portfolio(
            client_id=client.id,
            name=f"{client.name}'s Portfolio",
            status="active",
            created_at=datetime.utcnow(),
            created_by=user_id,
        )
        db.session.add(portfolio)
        db.session.flush()
    return portfolio


def _schedule_notes_for_client(client_id: int) -> Optional[str]:
    schedule = MonthlyInvestmentSchedule.query.filter_by(client_id=client_id, is_active=True).first()
    if schedule and getattr(schedule, "notes", None):
        return schedule.notes
    return None


def _create_workflow_for_investment(investment: MonthlyInvestment, user_id: int) -> Workflow:
    schedule_notes = _schedule_notes_for_client(investment.client_id)
    workflow = Workflow(
        monthly_investment_id=investment.id,
        current_stage="FUNDS",
        planned_amount=investment.planned_amount,
        investment_date=investment.investment_date,
        target_completion_date=investment.investment_date,
        created_by=user_id,
        schedule_notes=schedule_notes,
        notes="Workflow created for adhoc recommendation tracking",
    )
    db.session.add(workflow)
    db.session.flush()
    logger.info(
        "Created workflow %s for MonthlyInvestment %s (client_id=%s)",
        workflow.id,
        investment.id,
        investment.client_id,
    )
    return workflow


def get_or_create_workflow_for_adhoc_recommendation_session(
    recommendation_session,
    client_id: int,
    user_id: int,
) -> Optional[Workflow]:
    """
    Return a workflow for this recommendation session.

    - If one resolves (notes or latest active monthly investment), persist WORKFLOW_ID on the session
      when missing, then return it.
    - If none resolves and the session is not adhoc, return None.
    - If none resolves and session is adhoc: attach workflow to latest PENDING/ACTIVE investment if it
      lacks one; otherwise create a new MonthlyInvestment + Workflow for this cycle.
    """
    if not recommendation_session:
        return None

    wf = resolve_workflow_for_recommendation_session(recommendation_session, client_id)
    if wf:
        if _append_workflow_id_to_session_notes(recommendation_session, wf.id):
            db.session.commit()
        return wf

    if not _session_is_adhoc(recommendation_session):
        return None

    active_investment = (
        MonthlyInvestment.query.filter(
            MonthlyInvestment.client_id == client_id,
            MonthlyInvestment.status.in_(["PENDING", "ACTIVE", "pending", "active"]),
        )
        .order_by(MonthlyInvestment.id.desc())
        .first()
    )

    if active_investment and not active_investment.workflow:
        wf = _create_workflow_for_investment(active_investment, user_id)
        _append_workflow_id_to_session_notes(recommendation_session, wf.id)
        db.session.commit()
        return wf

    client = Client.query.get(client_id)
    if not client:
        logger.error("get_or_create_workflow: client %s not found", client_id)
        return None

    amt = recommendation_session.investment_amount
    if amt is None:
        planned = Decimal("0")
    else:
        planned = Decimal(str(amt))

    inv_date = date.today()
    portfolio = _get_or_create_portfolio(client, user_id)
    investment = MonthlyInvestment(
        client_id=client_id,
        portfolio_id=portfolio.id,
        planned_amount=planned,
        investment_date=inv_date,
        status="PENDING",
        created_by=user_id,
    )
    db.session.add(investment)
    db.session.flush()

    wf = _create_workflow_for_investment(investment, user_id)
    _append_workflow_id_to_session_notes(recommendation_session, wf.id)
    db.session.commit()
    logger.info(
        "Adhoc: created MonthlyInvestment %s and workflow %s for client_id=%s session_id=%s",
        investment.id,
        wf.id,
        client_id,
        recommendation_session.id,
    )
    return wf
