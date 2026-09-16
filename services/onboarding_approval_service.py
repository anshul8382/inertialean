"""Approval gates for lead proposals and onboarding invoices.

Rules:
- Advisor-created → manager or admin may approve
- Manager-created → admin may approve
- Admin-created → another admin may approve (not self unless sole admin path allowed via force)
- Client send (proposal email / Zoho invoice email) requires approved status
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

_APPROVAL_RE = re.compile(r"\[invoice_approval=([^\]]+)\]", re.I)
_APPROVED_BY_RE = re.compile(r"\[invoice_approved_by=(\d+)\]", re.I)
_APPROVED_AT_RE = re.compile(r"\[invoice_approved_at=([^\]]+)\]", re.I)


def _user_role(user) -> str:
    if not user:
        return "unknown"
    if getattr(user, "is_admin", False):
        return "admin"
    if getattr(user, "is_manager", False):
        return "manager"
    return "advisor"


def required_approver_role(creator) -> str:
    role = _user_role(creator)
    try:
        from models import BillingConfiguration

        if role == "advisor":
            row = BillingConfiguration.query.filter_by(
                config_key="onboarding_approve_advisor_work", is_active=True
            ).first()
            val = ((row.config_value if row else "") or "manager").strip().lower()
            return val if val in ("manager", "admin") else "manager"
        row = BillingConfiguration.query.filter_by(
            config_key="onboarding_approve_manager_work", is_active=True
        ).first()
        val = ((row.config_value if row else "") or "admin").strip().lower()
        return val if val in ("manager", "admin") else "admin"
    except Exception:
        pass
    if role == "advisor":
        return "manager"
    return "admin"


def can_approve(approver, creator) -> bool:
    """Return True if approver may approve work created by creator."""
    if not approver or not creator:
        return False
    if getattr(approver, "id", None) == getattr(creator, "id", None):
        return False
    need = required_approver_role(creator)
    if need == "manager":
        return bool(getattr(approver, "is_manager", False) or getattr(approver, "is_admin", False))
    return bool(getattr(approver, "is_admin", False))


def can_request_approval(user, creator_id: Optional[int]) -> bool:
    if not user:
        return False
    if creator_id is None:
        return True
    return int(user.id) == int(creator_id) or bool(
        getattr(user, "is_manager", False) or getattr(user, "is_admin", False)
    )


# --- Invoice markers (no schema change on invoice table) ---


def get_invoice_approval(invoice) -> Dict[str, Any]:
    notes = getattr(invoice, "notes", None) or ""
    m = _APPROVAL_RE.search(notes)
    status = (m.group(1).strip().lower() if m else "draft")
    by_m = _APPROVED_BY_RE.search(notes)
    at_m = _APPROVED_AT_RE.search(notes)
    return {
        "status": status if status in ("pending", "approved", "rejected") else "draft",
        "approved_by": int(by_m.group(1)) if by_m else None,
        "approved_at": at_m.group(1) if at_m else None,
        "is_approved": status == "approved",
        "is_pending": status == "pending",
    }


def _set_invoice_marker(invoice, key: str, value: str) -> None:
    notes = invoice.notes or ""
    pattern = re.compile(rf"\[{re.escape(key)}=[^\]]*\]")
    marker = f"[{key}={value}]"
    if pattern.search(notes):
        invoice.notes = pattern.sub(marker, notes)
    else:
        invoice.notes = f"{notes}\n{marker}".strip() if notes else marker


def set_invoice_pending_approval(invoice) -> None:
    _set_invoice_marker(invoice, "invoice_approval", "pending")


def set_invoice_approved(invoice, *, approved_by_id: int) -> None:
    _set_invoice_marker(invoice, "invoice_approval", "approved")
    _set_invoice_marker(invoice, "invoice_approved_by", str(approved_by_id))
    _set_invoice_marker(invoice, "invoice_approved_at", datetime.utcnow().isoformat(timespec="seconds"))


def invoice_may_send_to_client(invoice) -> Tuple[bool, str]:
    info = get_invoice_approval(invoice)
    if info["is_approved"]:
        return True, ""
    if info["is_pending"]:
        return False, "Invoice is pending approval. Approve before sending to the client."
    return False, "Invoice must be approved before sending to the client."


# --- Proposal status helpers ---


def proposal_is_approved(proposal) -> bool:
    return (getattr(proposal, "status", None) or "").lower() in ("approved", "sent")


def proposal_may_send_to_client(proposal) -> Tuple[bool, str]:
    st = (getattr(proposal, "status", None) or "").lower()
    if st == "approved":
        return True, ""
    if st == "sent":
        return False, "Proposal was already sent."
    if st == "pending_approval":
        return False, "Proposal is pending approval."
    return False, "Proposal must be approved before sending to the client."


def submit_proposal_for_approval(proposal) -> None:
    proposal.status = "pending_approval"
    proposal.updated_at = datetime.utcnow()


def approve_proposal(proposal, *, approved_by_id: int) -> None:
    proposal.status = "approved"
    details = dict(proposal.details_json or {})
    details["approved_by"] = approved_by_id
    details["approved_at"] = datetime.utcnow().isoformat(timespec="seconds")
    proposal.details_json = details
    proposal.updated_at = datetime.utcnow()
