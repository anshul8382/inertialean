"""Create a billing client shell for a lead without finalizing conversion."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.exc import IntegrityError

from extensions import db
from models import AgreementVariables, Client, Lead
from services.lead_conversion_service import find_client_by_email

logger = logging.getLogger(__name__)


def lead_is_fully_converted(lead: Lead) -> bool:
    return bool(lead and lead.client_id and (lead.status or "").lower() == "onboarding_completed")


def ensure_client_shell_for_lead(lead: Lead, *, acting_user_id: Optional[int] = None) -> Tuple[Client, bool]:
    """
    Ensure a Client exists and is linked on the lead for invoicing.

    Does NOT set status to onboarding_completed — that remains convert/finalize.
    Returns (client, created_new).
    """
    if lead is None:
        raise ValueError("Lead is required")

    if lead.client_id:
        client = Client.query.get(lead.client_id)
        if client:
            _link_agreement_vars(lead.id, client.id)
            return client, False

    email = (lead.email or "").strip()
    name = (lead.name or "").strip() or "Client"
    if not email:
        raise ValueError("Lead email is required to create a client for invoicing.")

    user_id = acting_user_id or lead.user_id or 1
    created_new = False
    client = find_client_by_email(email)

    if client is None:
        # Prefer risk profile from latest assessment when available
        risk = None
        try:
            from services.risk_assessment_service import latest_for_lead

            sub = latest_for_lead(lead.id)
            if sub and sub.client_risk_profile:
                risk = sub.client_risk_profile
            elif sub and sub.risk_profile:
                risk = (sub.risk_profile or "").lower().replace(" ", "_")
        except Exception:
            pass

        client = Client(
            name=name[:100],
            email=email[:120],
            phone=(lead.phone or "")[:20] or None,
            risk_profile=risk,
            user_id=user_id,
            advisor_id=lead.user_id or user_id,
            created_at=datetime.utcnow(),
            is_active=True,
        )
        db.session.add(client)
        try:
            db.session.flush()
            created_new = True
        except IntegrityError:
            db.session.rollback()
            lead = Lead.query.get(lead.id)
            client = find_client_by_email(email)
            if client is None:
                raise ValueError(f'Could not create or find client for "{email}".')
            created_new = False

    lead.client_id = client.id
    _link_agreement_vars(lead.id, client.id)
    db.session.flush()
    logger.info(
        "Client shell ready for lead %s → client %s (created=%s)",
        lead.id,
        client.id,
        created_new,
    )
    return client, created_new


def _link_agreement_vars(lead_id: int, client_id: int) -> None:
    AgreementVariables.query.filter_by(lead_id=lead_id, client_id=None).update(
        {"client_id": client_id},
        synchronize_session=False,
    )
