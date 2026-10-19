"""Delete a lead after clearing owned onboarding rows (no silent FK failures)."""
from __future__ import annotations

import logging

from extensions import db

logger = logging.getLogger(__name__)


def _has_table(name: str) -> bool:
    try:
        from sqlalchemy import inspect

        return inspect(db.engine).has_table(name)
    except Exception:
        return False


def _onboarding_models():
    from models.lead_onboarding import LeadKycProfile, LeadProposal, RiskAssessmentSubmission

    return LeadKycProfile, LeadProposal, RiskAssessmentSubmission


def _clear_agreement_billing_for_lead(lead_id: int) -> None:
    """
    Remove billing_rate_structure / billing_schedule rows for the lead's agreements.

    Lead → Agreement uses ORM delete-orphan. Without this (or cascade on Agreement),
    SQLAlchemy nulls BillingRateStructure.agreement_id, which is NOT NULL → 1048.
    """
    from models import Agreement, BillingRateStructure, BillingSchedule

    agreement_ids = [
        row[0]
        for row in db.session.query(Agreement.id).filter_by(lead_id=lead_id).all()
    ]
    if not agreement_ids:
        return

    BillingRateStructure.query.filter(
        BillingRateStructure.agreement_id.in_(agreement_ids)
    ).delete(synchronize_session="fetch")
    BillingSchedule.query.filter(
        BillingSchedule.agreement_id.in_(agreement_ids)
    ).delete(synchronize_session="fetch")


def delete_lead_record(lead) -> None:
    """Remove KYC/proposal rows, unlink risk quizzes, then delete the lead. Caller commits."""
    lead_id = lead.id
    try:
        LeadKycProfile, LeadProposal, RiskAssessmentSubmission = _onboarding_models()

        if _has_table("lead_kyc_profile"):
            LeadKycProfile.query.filter_by(lead_id=lead_id).delete(synchronize_session=False)
        if _has_table("lead_proposal"):
            LeadProposal.query.filter_by(lead_id=lead_id).delete(synchronize_session=False)
        if _has_table("risk_assessment_submission"):
            RiskAssessmentSubmission.query.filter_by(lead_id=lead_id).update(
                {"lead_id": None}, synchronize_session=False
            )
    except Exception:
        logger.exception("Could not clear onboarding rows for lead %s before delete", lead_id)
        raise

    try:
        _clear_agreement_billing_for_lead(lead_id)
    except Exception:
        logger.exception(
            "Could not clear agreement billing rows for lead %s before delete", lead_id
        )
        raise

    db.session.delete(lead)
