"""
Resolve ``MonthlyInvestment.portfolio_id`` when missing.

Most clients have a single active portfolio; this matches ``new_monthly_investment`` logic:
active portfolio first, else first portfolio row, else create a default portfolio.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from extensions import db
from models import Client, MonthlyInvestment, Portfolio

logger = logging.getLogger(__name__)


def get_or_create_portfolio_for_client(client_id: int, user_id: int) -> Portfolio:
    """Return an active (or first) portfolio for the client, creating one if needed."""
    client = Client.query.get(client_id)
    if not client:
        raise ValueError(f"Client {client_id} not found")

    portfolio = Portfolio.query.filter_by(client_id=client_id, status="active").first()
    if not portfolio and getattr(client, "portfolios", None):
        portfolio = client.portfolios[0]
    if not portfolio:
        portfolio = Portfolio(
            client_id=client_id,
            name=f"{client.name}'s Portfolio",
            created_at=datetime.utcnow(),
            status="active",
            created_by=user_id,
        )
        db.session.add(portfolio)
        db.session.flush()
        logger.info(
            "Created default portfolio id=%s for client_id=%s (user_id=%s)",
            portfolio.id,
            client_id,
            user_id,
        )
    return portfolio


def ensure_monthly_investment_portfolio_id(mi: MonthlyInvestment, user_id: int) -> bool:
    """
    If ``mi.portfolio_id`` is missing, set it from ``get_or_create_portfolio_for_client``.

    Returns True if the ORM object was mutated (caller should flush/commit).
    """
    if mi.portfolio_id:
        return False
    pf = get_or_create_portfolio_for_client(mi.client_id, user_id)
    mi.portfolio_id = pf.id
    return True


def backfill_null_monthly_investment_portfolio_ids(user_id: int) -> dict[str, Any]:
    """
    One-shot / admin: set ``portfolio_id`` on all ``monthly_investment`` rows where it is NULL.

    Commits per successful row so one bad client does not roll back the whole batch.
    """
    result: dict[str, Any] = {"scanned": 0, "updated": 0, "errors": []}
    rows = MonthlyInvestment.query.filter(MonthlyInvestment.portfolio_id.is_(None)).all()
    for mi in rows:
        result["scanned"] += 1
        try:
            if ensure_monthly_investment_portfolio_id(mi, user_id):
                db.session.commit()
                result["updated"] += 1
        except Exception as e:
            db.session.rollback()
            logger.exception("backfill portfolio_id failed for monthly_investment id=%s", mi.id)
            result["errors"].append(f"monthly_investment {mi.id} (client {mi.client_id}): {e}")
    return result
