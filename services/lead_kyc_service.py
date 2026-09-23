"""Simple KYC readiness for lead onboarding (document-based; no CKYC API yet)."""
from __future__ import annotations

from typing import List, Optional

KYC_DOCUMENT_TYPES = frozenset(
    {
        "kyc",
        "pan",
        "address proof",
        "address_proof",
        "aadhaar",
        "aadhar",
        "passport",
        "ckyc",
    }
)


def _norm_type(document_type: Optional[str]) -> str:
    return (document_type or "").strip().lower()


def list_kyc_documents(lead_id: int) -> List[dict]:
    from services.lead_document_service import list_lead_documents

    try:
        docs = list_lead_documents(lead_id)
    except Exception:
        return []
    return [d for d in docs if _norm_type(d.get("document_type")) in KYC_DOCUMENT_TYPES]


def lead_has_kyc_documents(lead_id: int) -> bool:
    if list_kyc_documents(lead_id):
        return True
    try:
        from services.lead_kyc_profile_service import get_or_create_profile, kyc_table_ready

        if kyc_table_ready():
            profile = get_or_create_profile(lead_id)
            if profile and (profile.status or "") == "verified" and (profile.provider or "") != "manual":
                return True
    except Exception:
        pass
    return False


def lead_has_kyc_pan(lead_id: int) -> bool:
    try:
        from services.regulatory_identity_capture_service import lead_has_regulatory_pan

        return lead_has_regulatory_pan(lead_id)
    except Exception:
        return False


def mark_kyc_complete(lead) -> None:
    """Set lead status to kyc_uploaded when still pre-invoice stages."""
    from datetime import datetime

    from extensions import db

    if not lead_has_kyc_pan(lead.id):
        raise ValueError(
            "Save a valid PAN on the KYC profile before marking KYC complete "
            "(needed for regulatory client master)."
        )

    note = "[KYC] Documents uploaded / marked complete (manual — CKYC API not connected yet)."
    existing = (lead.notes or "").strip()
    if "[KYC]" not in existing:
        lead.notes = f"{existing}\n{note}".strip() if existing else note

    early = {
        None,
        "",
        "new",
        "contacted",
        "qualified",
        "risk_profile_sent",
        "risk_profile_received",
        "proposal_sent",
        "agreement_sent",
        "agreement_signed",
        "agreement_reviewed",
    }
    if (lead.status or "").lower() in early or lead.status == "agreement_signed":
        # Prefer not to overwrite agreement_signed until docs exist — use kyc_uploaded
        if lead_has_kyc_documents(lead.id):
            lead.status = "kyc_uploaded"
    lead.updated_at = datetime.utcnow()
    try:
        from services.regulatory_identity_capture_service import sync_kyc_pan_to_lead_agreements

        sync_kyc_pan_to_lead_agreements(lead.id)
    except Exception:
        pass
    db.session.commit()
