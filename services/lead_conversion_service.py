"""Convert CRM leads to clients (link or create)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Agreement, AgreementVariables, Client, Lead, Meeting

# Match agreement overview / invoice "usable" statuses. Require PDF path so
# draft/generated-without-file cannot unlock convert.
CONVERTIBLE_AGREEMENT_STATUSES = frozenset({"signed", "active", "completed"})

_CONVERT_BLOCK_MESSAGE = (
    "Upload or generate a signed agreement with a PDF before converting this lead "
    "to a client. Open Agreements on the lead to record an existing signed PDF "
    "or mark a generated agreement as signed."
)

_CONVERT_BLOCK_INVOICE = (
    "Generate the onboarding invoice and mark it as paid before converting this lead "
    "to a client."
)

_CONVERT_BLOCK_KYC = (
    "Upload KYC documents (PAN, address proof, or KYC pack) on the lead before converting. "
    "CKYC API integration is not connected yet — document upload is required."
)


def find_client_by_email(email: str) -> Optional[Client]:
    normalized = (email or "").strip().lower()
    if not normalized:
        return None
    return Client.query.filter(func.lower(Client.email) == normalized).first()


def _agreement_has_pdf(agreement: Agreement) -> bool:
    path = (agreement.generated_pdf_path or "").strip()
    if not path:
        return False
    try:
        from flask import has_app_context

        from services.agreement_pdf_paths import agreement_pdf_abs_path

        if has_app_context():
            return bool(agreement_pdf_abs_path(path))
    except Exception:
        pass
    # Outside app context (unit tests / scripts): path string is enough signal.
    return True


def lead_convertible_agreements(lead: Lead) -> list:
    """Agreements that unlock convert: signed/active/completed with a PDF."""
    if lead is None or lead.id is None:
        return []
    rows = Agreement.query.filter_by(lead_id=lead.id).all()
    out = []
    for ag in rows:
        status = (ag.status or "").strip().lower()
        if status not in CONVERTIBLE_AGREEMENT_STATUSES:
            continue
        if not _agreement_has_pdf(ag):
            continue
        out.append(ag)
    return out


def lead_has_convertible_agreement(lead: Lead) -> bool:
    return bool(lead_convertible_agreements(lead))


def lead_is_fully_converted(lead: Lead) -> bool:
    """True when client exists and onboarding checklist was closed."""
    return bool(
        lead
        and lead.client_id
        and (lead.status or "").lower() == "onboarding_completed"
    )


def lead_has_client_profile(lead: Lead) -> bool:
    """True when a Client row is linked (enough to send recommendations)."""
    return bool(lead and lead.client_id)


def convert_block_reason(lead: Lead) -> Optional[str]:
    """None if convert is allowed; otherwise a user-facing reason.

    KYC / agreement / paid invoice do NOT block conversion — those stay open on
    the onboarding checklist so the client profile can activate for first recos.
    """
    if lead is None:
        return "Lead not found."
    try:
        from services.lead_classification_service import is_career_application
    except ImportError:

        def is_career_application(_lead):
            return False

    if is_career_application(lead):
        return (
            "This is a career application, not a sales lead. "
            "It cannot be converted to a client."
        )
    if lead_is_fully_converted(lead):
        return None
    return None


def require_convertible_agreement(lead: Lead) -> None:
    """Compatibility wrapper: only raises when convert_block_reason is set."""
    reason = convert_block_reason(lead)
    if reason and not lead_has_client_profile(lead):
        raise ValueError(reason)


def onboarding_checklist_incomplete(lead: Lead) -> list:
    """Open checklist items after early convert (informational)."""
    missing = []
    try:
        if not lead_has_convertible_agreement(lead):
            missing.append("signed agreement")
    except Exception:
        missing.append("signed agreement")
    try:
        from services.lead_kyc_service import lead_has_kyc_documents, lead_has_kyc_pan

        if not lead_has_kyc_documents(lead.id):
            missing.append("KYC")
        elif not lead_has_kyc_pan(lead.id):
            missing.append("PAN on KYC")
    except Exception:
        missing.append("KYC")
    try:
        from services.lead_onboarding_billing_service import lead_has_paid_invoice

        if not lead_has_paid_invoice(lead):
            missing.append("paid invoice")
    except Exception:
        missing.append("paid invoice")
    return missing


def convert_lead_to_client(
    lead: Lead,
    *,
    name: str,
    email: str,
    phone: Optional[str],
    risk_profile: str,
    referral_by: Optional[str],
    acting_user_id: int,
) -> Tuple[Client, bool]:
    """
    Convert a lead to a client record.

    Returns (client, created_new). When email already exists on a client,
    links the lead to that client instead of inserting a duplicate row.

    Does not require KYC / paid invoice / signed agreement — those remain on the
    open onboarding checklist so recommendations can start from the client profile.
    """
    name = (name or "").strip()
    email = (email or "").strip()
    if not name or not email:
        raise ValueError("Name and email are required.")

    if lead_is_fully_converted(lead):
        client = Client.query.get(lead.client_id)
        if client:
            return client, False
        raise ValueError("Lead is already converted but the client record is missing.")

    require_convertible_agreement(lead)

    lead_id = lead.id
    created_new = False

    if lead.client_id:
        client = Client.query.get(lead.client_id)
        if not client:
            raise ValueError(
                "Lead client shell is missing; recreate from Onboarding → Invoice."
            )
    else:
        client = find_client_by_email(email)
        if client is None:
            client = Client(
                name=name,
                email=email,
                phone=phone,
                risk_profile=risk_profile,
                user_id=acting_user_id,
                advisor_id=lead.user_id or acting_user_id,
            )
            db.session.add(client)
            try:
                db.session.flush()
                created_new = True
            except IntegrityError:
                db.session.rollback()
                lead = Lead.query.get(lead_id)
                if lead is None:
                    raise ValueError("Lead not found after duplicate-email conflict.")
                client = find_client_by_email(email)
                if client is None:
                    raise ValueError(
                        f'A client with email "{email}" already exists. '
                        "Please open that client or use a different email."
                    )

    client.name = name[:100]
    client.email = email[:120]
    if phone:
        client.phone = (phone or "")[:20]
    if risk_profile:
        client.risk_profile = risk_profile
        if hasattr(client, "risk_profile_updated_at"):
            client.risk_profile_updated_at = datetime.utcnow()

    _finalize_lead_conversion(lead, client, referral_by=referral_by)
    return client, created_new


def _finalize_lead_conversion(
    lead: Lead,
    client: Client,
    *,
    referral_by: Optional[str],
) -> None:
    lead.client_id = client.id
    if referral_by:
        lead.referral_by = referral_by
    if hasattr(lead, "converted_at"):
        lead.converted_at = datetime.utcnow()

    # Close onboarding only when checklist is complete; otherwise keep open.
    incomplete = onboarding_checklist_incomplete(lead)
    if incomplete:
        if (lead.status or "").lower() != "onboarding_completed":
            lead.status = "onboarding_started"
    else:
        lead.status = "onboarding_completed"

    for meeting in Meeting.query.filter_by(lead_id=lead.id).all():
        meeting.client_id = client.id

    AgreementVariables.query.filter_by(lead_id=lead.id, client_id=None).update(
        {"client_id": client.id},
        synchronize_session=False,
    )

    # Capture PAN on agreement variables for regulatory client master going forward.
    try:
        from services.regulatory_identity_capture_service import (
            ensure_regulatory_fields_on_agreement,
            sync_kyc_pan_to_lead_agreements,
        )

        sync_kyc_pan_to_lead_agreements(lead.id)
        for ag in lead_convertible_agreements(lead):
            ensure_regulatory_fields_on_agreement(ag, lead.id, client.id)
    except Exception:
        pass
