"""
Update ``Workflow.actual_amount`` from executed trade recommendations (session-scoped).

Net = sum of (quantity × execution price) for ``status == 'executed'`` rows,
with buy positive and sell negative — aligned with trade-detail notionals on ``Recommendation``.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Iterable

from extensions import db
from models import Recommendation, RecommendationSession
from services.recommendation_workflow_link_service import resolve_workflow_for_recommendation_session

logger = logging.getLogger(__name__)


def net_executed_notional_for_recommendations(recommendations: Iterable[Recommendation]) -> Decimal:
    """Signed net notional for executed buy/sell rows (ignores hold and non-executed)."""
    total = Decimal("0")
    for rec in recommendations:
        if (rec.status or "").strip().lower() != "executed":
            continue
        if rec.quantity is None:
            continue
        price = rec.actual_price if rec.actual_price is not None else rec.target_price
        if price is None:
            continue
        qty = Decimal(str(rec.quantity))
        val = qty * Decimal(str(price))
        action = (rec.action or "").strip().lower()
        if action == "sell":
            total -= val
        elif action == "buy":
            total += val
    return total


def sync_workflow_actual_amount_for_session(session_id: int, client_id: int) -> bool:
    """
    Resolve workflow for this recommendation session and set ``actual_amount`` to the
    net executed notional for all recommendations in the session.

    Returns True if a workflow was found and updated (caller should commit).
    """
    if not session_id or not client_id:
        return False
    sess = RecommendationSession.query.get(session_id)
    if not sess:
        return False
    wf = resolve_workflow_for_recommendation_session(sess, int(client_id))
    if not wf:
        logger.debug(
            "sync_workflow_actual_amount: no workflow for session_id=%s client_id=%s",
            session_id,
            client_id,
        )
        return False
    recs = Recommendation.query.filter_by(
        session_id=int(session_id),
        client_id=int(client_id),
    ).all()
    net = net_executed_notional_for_recommendations(recs)
    wf.actual_amount = net
    db.session.add(wf)
    return True
