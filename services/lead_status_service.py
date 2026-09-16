"""Canonical lead onboarding statuses + SLA / workflow mapping."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

# (value, label) — single source for UI selects
LEAD_STATUS_CHOICES: List[Tuple[str, str]] = [
    ("new", "New"),
    ("contacted", "Contacted"),
    ("qualified", "Qualified"),
    ("risk_profile_sent", "Risk Profile Sent"),
    ("risk_profile_received", "Risk Profile Received"),
    ("proposal_sent", "Proposal Sent"),
    ("proposal_reviewed", "Proposal Reviewed"),
    ("proposal_revised", "Proposal Revised"),
    ("agreement_sent", "Agreement Sent"),
    ("agreement_reviewed", "Agreement Reviewed"),
    ("agreement_signed", "Agreement Signed"),
    ("kyc_uploaded", "KYC Uploaded"),
    ("onboarding_started", "Onboarding Started"),
    ("onboarding_completed", "Onboarding Completed"),
    ("dropped", "Dropped"),
    # Legacy aliases kept selectable for old records
    ("referral", "Referral (legacy)"),
    ("meeting", "Meeting (legacy)"),
    ("risk_profile", "Risk Profile (legacy)"),
    ("proposal", "Proposal (legacy)"),
    ("acceptance", "Acceptance (legacy)"),
    ("agreement", "Agreement (legacy)"),
    ("agreement_signing", "Agreement Signing (legacy)"),
    ("kyc", "KYC (legacy)"),
    ("invoice", "Invoice (legacy)"),
    ("payment", "Payment (legacy)"),
    ("onboarding", "Onboarding (legacy)"),
    ("welcome_recommendation", "Welcome Recommendation (legacy)"),
]

# Modern statuses only (for add/edit/list quick-update UI)
MODERN_LEAD_STATUS_CHOICES: List[Tuple[str, str]] = [
    (v, label) for v, label in LEAD_STATUS_CHOICES if "(legacy)" not in label
]

# status -> workflow stage + SLA hours + description
LEAD_STATUS_CONFIG: Dict[str, Dict] = {
    "new": {"stage": "REFERRAL", "sla_hours": 24, "description": "New lead should be contacted within 24 hrs"},
    "contacted": {"stage": "MEETING", "sla_hours": 72, "description": "Follow up within 72 hrs of contact"},
    "qualified": {"stage": "RISK_PROFILE", "sla_hours": 168, "description": "Send risk profile / proposal within 1 week"},
    "risk_profile_sent": {"stage": "RISK_PROFILE", "sla_hours": 72, "description": "Await risk profile within 72 hrs"},
    "risk_profile_received": {"stage": "PROPOSAL", "sla_hours": 72, "description": "Send proposal within 72 hrs of risk profile"},
    "proposal_sent": {"stage": "PROPOSAL", "sla_hours": 168, "description": "Follow up on proposal within 1 week"},
    "proposal_reviewed": {"stage": "ACCEPTANCE", "sla_hours": 72, "description": "Address feedback within 72 hrs"},
    "proposal_revised": {"stage": "PROPOSAL", "sla_hours": 168, "description": "Follow up on revised proposal within 1 week"},
    "agreement_sent": {"stage": "AGREEMENT_SIGNING", "sla_hours": 168, "description": "Follow up on agreement within 1 week"},
    "agreement_reviewed": {"stage": "AGREEMENT_SIGNING", "sla_hours": 72, "description": "Address agreement feedback within 72 hrs"},
    "agreement_signed": {"stage": "KYC", "sla_hours": 72, "description": "Complete KYC within 72 hrs of signing"},
    "kyc_uploaded": {"stage": "INVOICE", "sla_hours": 48, "description": "Generate invoice within 48 hrs of KYC"},
    "onboarding_started": {"stage": "PAYMENT", "sla_hours": 168, "description": "Collect payment / finish onboarding"},
    "onboarding_completed": {"stage": "COMPLETED", "sla_hours": 0, "description": "Onboarding completed"},
    "dropped": {"stage": "COMPLETED", "sla_hours": 0, "description": "Lead has been dropped"},
    # Legacy
    "referral": {"stage": "REFERRAL", "sla_hours": 24, "description": "New referral should be contacted within 24 hrs"},
    "meeting": {"stage": "MEETING", "sla_hours": 336, "description": "Meeting should be set up within 2 weeks"},
    "risk_profile": {"stage": "RISK_PROFILE", "sla_hours": 72, "description": "Risk profile to be submitted by client within 72 hrs of meeting"},
    "proposal": {"stage": "PROPOSAL", "sla_hours": 24, "description": "Proposal should be sent within 24 hrs of meeting"},
    "acceptance": {"stage": "ACCEPTANCE", "sla_hours": 336, "description": "Acceptance within 2 weeks of sending proposal"},
    "agreement": {"stage": "AGREEMENT", "sla_hours": 24, "description": "Agreement should be sent within 24 hours of acceptance"},
    "agreement_signing": {"stage": "AGREEMENT_SIGNING", "sla_hours": 336, "description": "Agreement signing to be done by client in 2 weeks"},
    "kyc": {"stage": "KYC", "sla_hours": 24, "description": "KYC within 24 hours of signing agreement"},
    "invoice": {"stage": "INVOICE", "sla_hours": 24, "description": "Invoice to be sent within 24 hrs of signing agreement"},
    "payment": {"stage": "PAYMENT", "sla_hours": 168, "description": "Payment to be received within 7 days of sending invoice"},
    "onboarding": {"stage": "ONBOARDING", "sla_hours": 24, "description": "Onboarding to be done within 24 hrs of payment"},
    "welcome_recommendation": {"stage": "WELCOME_RECOMMENDATION", "sla_hours": 48, "description": "Welcome and recommendation within 48 hrs of payment"},
}


def sla_hours_for_status(status: Optional[str]) -> int:
    if not status:
        return 0
    cfg = LEAD_STATUS_CONFIG.get(status) or LEAD_STATUS_CONFIG.get((status or "").lower())
    return int(cfg["sla_hours"]) if cfg else 0


def status_config(status: Optional[str]) -> Optional[Dict]:
    if not status:
        return None
    return LEAD_STATUS_CONFIG.get(status) or LEAD_STATUS_CONFIG.get(status.lower())


def build_status_note_line(
    new_status: str,
    notes: Optional[str] = None,
    when: Optional[datetime] = None,
) -> str:
    stamp = (when or datetime.utcnow()).strftime("%Y-%m-%d")
    line = f"[{stamp}] Status → {new_status}"
    extra = (notes or "").strip()
    if extra:
        line += f": {extra}"
    return line


def merge_status_change_notes(
    existing: Optional[str],
    new_status: str,
    notes: Optional[str] = None,
    when: Optional[datetime] = None,
) -> Optional[str]:
    """Append a status-change line to visible notes; keep IC questionnaire/tag markers."""
    from services.content_intelligence_person_source import merge_lead_notes_edit, strip_ic_markers

    line = build_status_note_line(new_status, notes, when=when)
    human = (strip_ic_markers(existing) or "").strip()
    combined = f"{human}\n{line}".strip() if human else line
    return merge_lead_notes_edit(existing, combined)


def apply_status_fields(lead, new_status: str, notes: Optional[str] = None) -> None:
    """Set status, optional dropped/inactive, and append status notes. Caller commits."""
    lead.status = new_status
    if new_status == "dropped":
        lead.is_active = False
    lead.notes = merge_status_change_notes(lead.notes, new_status, notes)
    lead.updated_at = datetime.utcnow()
