"""Lead KYC profile + CKYC-ready verification (manual provider until CERSAI API is connected)."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional, Protocol

from extensions import db

logger = logging.getLogger(__name__)


class CkycProvider(Protocol):
    def verify(self, profile) -> Dict[str, Any]:
        ...


class ManualCkycProvider:
    """Local validation only; manual capture does not imply verified KYC."""

    name = "manual"

    def verify(self, profile) -> Dict[str, Any]:
        pan = (profile.pan or "").strip().upper()
        if pan and not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", pan):
            return {"ok": False, "error": "Invalid PAN format."}
        if profile.ckyc_number and len(profile.ckyc_number.strip()) >= 10:
            return {
                "ok": True,
                "status": "submitted",
                "provider_ref": profile.ckyc_number.strip(),
                "message": "CKYC number recorded for manual review.",
            }
        if pan and profile.aadhaar_last4:
            return {
                "ok": True,
                "status": "submitted",
                "message": "KYC details saved. Add CKYC number when available, or upload documents.",
            }
        return {"ok": False, "error": "Enter PAN and Aadhaar last 4, or a CKYC number."}


def get_ckyc_provider() -> ManualCkycProvider:
    return ManualCkycProvider()


def kyc_table_ready() -> bool:
    try:
        from sqlalchemy import inspect

        return inspect(db.engine).has_table("lead_kyc_profile")
    except Exception:
        return False


def get_or_create_profile(lead_id: int):
    if not kyc_table_ready():
        return None
    from models.lead_onboarding import LeadKycProfile

    row = LeadKycProfile.query.filter_by(lead_id=lead_id).first()
    if row:
        return row
    row = LeadKycProfile(lead_id=lead_id)
    db.session.add(row)
    db.session.flush()
    return row


def save_kyc_profile(lead_id: int, form_data: Dict[str, Any], *, verify: bool = True) -> Dict[str, Any]:
    if not kyc_table_ready():
        return {"error": "KYC table missing. Run: python migrations/add_lead_kyc_profile_table.py"}

    pan = (form_data.get("pan") or "").strip().upper()[:10] or None
    aadhaar_last4 = (form_data.get("aadhaar_last4") or "").strip()[:4] or None
    ckyc_number = (form_data.get("ckyc_number") or "").strip()[:32] or None
    if pan and not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", pan):
        return {"error": "Invalid PAN format."}
    if aadhaar_last4 and not re.match(r"^[0-9]{4}$", aadhaar_last4):
        return {"error": "Aadhaar last 4 must be exactly four digits."}
    if ckyc_number and len(ckyc_number) < 10:
        return {"error": "CKYC number must be at least 10 characters."}

    profile = get_or_create_profile(lead_id)
    if profile is None:
        return {"error": "Could not create KYC profile."}
    profile.pan = pan
    profile.aadhaar_last4 = aadhaar_last4
    profile.ckyc_number = ckyc_number
    profile.full_name_as_per_pan = (form_data.get("full_name_as_per_pan") or "").strip()[:120] or None
    profile.notes = (form_data.get("notes") or "").strip() or profile.notes
    profile.provider = "manual"
    profile.updated_at = datetime.utcnow()

    result: Dict[str, Any] = {"success": True, "profile_id": profile.id}
    if verify:
        provider = get_ckyc_provider()
        outcome = provider.verify(profile)
        if not outcome.get("ok"):
            profile.status = "pending"
            return {"error": outcome.get("error") or "KYC validation failed"}
        profile.status = outcome.get("status") or "submitted"
        profile.provider_ref = outcome.get("provider_ref")
        if profile.status == "verified":
            profile.verified_at = datetime.utcnow()
        result["message"] = outcome.get("message")
        try:
            from models import Lead
            from services.lead_kyc_service import lead_has_kyc_documents, mark_kyc_complete

            lead = Lead.query.get(lead_id)
            if lead and lead_has_kyc_documents(lead_id):
                mark_kyc_complete(lead)
        except Exception:
            logger.debug("Lead KYC status promote skipped", exc_info=True)

    db.session.commit()
    return result
