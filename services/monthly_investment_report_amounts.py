"""
Effective amounts for monthly-investment ops reports.

Priority for a workflow-backed investment:
1. ``Workflow.actual_amount`` when recorded (execution / completion)
2. Sent amount when recommendations have been sent (NOTIFY+)
3. Planned amount otherwise (FUNDS / RECOS / no workflow)

Sent amount = sum of linked ``RecommendationSession.investment_amount`` values
(``WORKFLOW_ID`` in session notes), with a line-sum fallback when investment_amount
is unset — same convention as BNI TY notes.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from models import MonthlyInvestment, Recommendation, RecommendationSession, Workflow
from services.recommendation_workflow_link_service import extract_workflow_id_from_notes

# Stages at/after client notification = recommendations considered sent
STAGES_RECOS_SENT = frozenset({"NOTIFY", "EXEC", "UPDATE", "COMPLETED"})


def _as_float(value) -> float:
    if value is None:
        return 0.0
    return float(value)


def _signed_line_amount(rec: Recommendation) -> Decimal:
    if rec.quantity is None:
        return Decimal("0")
    price = rec.actual_price if rec.actual_price is not None else rec.target_price
    if price is None:
        return Decimal("0")
    qty = Decimal(str(rec.quantity))
    val = qty * Decimal(str(price))
    action = (rec.action or "").strip().lower()
    if action == "sell":
        return -val
    if action == "buy":
        return val
    return Decimal("0")


def _session_investment_decimal(session: RecommendationSession, client_id: int) -> Decimal:
    if session.investment_amount is not None:
        return Decimal(str(session.investment_amount))
    recs = Recommendation.query.filter_by(session_id=session.id, client_id=client_id).all()
    return sum((_signed_line_amount(r) for r in recs), Decimal("0"))


def sent_amount_for_workflow(workflow: Workflow, client_id: int) -> Optional[float]:
    """
    Net sent / recommended investment for sessions linked to this workflow.
    Returns None when no linked sessions exist.
    """
    if not workflow or not client_id:
        return None
    sessions = RecommendationSession.query.filter_by(client_id=client_id).all()
    linked = [s for s in sessions if extract_workflow_id_from_notes(s.notes) == workflow.id]
    if not linked:
        return None
    total = sum((_session_investment_decimal(s, client_id) for s in linked), Decimal("0"))
    return float(total)


def _recos_considered_sent(workflow: Optional[Workflow]) -> bool:
    if not workflow:
        return False
    stage = (workflow.current_stage or "").strip().upper()
    if stage in ("ICR", "INVESTMENT/CHANGES/REDEMPTION"):
        stage = "FUNDS"
    return stage in STAGES_RECOS_SENT


def planned_amount_for_investment(investment: MonthlyInvestment) -> float:
    return _as_float(investment.planned_amount)


def actual_amount_for_investment(investment: MonthlyInvestment) -> Optional[float]:
    wf = investment.workflow
    if wf is not None and wf.actual_amount is not None:
        return float(wf.actual_amount)
    return None


def effective_amount_for_investment(investment: MonthlyInvestment) -> float:
    """
    Amount to use in report totals / pending rows:

    - actual when recorded
    - else sent amount when recos are sent (NOTIFY+)
    - else planned
    """
    actual = actual_amount_for_investment(investment)
    if actual is not None:
        return actual

    wf = investment.workflow
    if _recos_considered_sent(wf):
        client_id = investment.client_id
        sent = sent_amount_for_workflow(wf, client_id) if wf else None
        if sent is not None:
            return sent

    return planned_amount_for_investment(investment)


def completed_amount_for_investment(investment: MonthlyInvestment) -> float:
    """Completed bucket: prefer actual; fall back to planned only if actual missing."""
    actual = actual_amount_for_investment(investment)
    if actual is not None:
        return actual
    return planned_amount_for_investment(investment)
