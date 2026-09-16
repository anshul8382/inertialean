"""Ready-made WhatsApp message drafts for lead onboarding (backed by playbook .md)."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional
from urllib.parse import quote


def _digits_phone(phone: Optional[str]) -> str:
    raw = re.sub(r"\D", "", phone or "")
    if len(raw) == 10:
        return "91" + raw
    if raw.startswith("0") and len(raw) == 11:
        return "91" + raw[1:]
    return raw


def wa_me_url(phone: Optional[str], text: str) -> Optional[str]:
    digits = _digits_phone(phone)
    if not digits:
        return None
    return f"https://wa.me/{digits}?text={quote(text)}"


def risk_profile_draft(lead, *, quiz_url: str, sender_name: str = "Inertia") -> Dict[str, Any]:
    from services.lead_message_playbook_service import render_stage

    return render_stage(
        "risk_invite",
        lead=lead,
        sender_name=sender_name,
        quiz_url=quiz_url,
    )


def followup_draft(lead, *, sender_name: str = "Inertia", note: str = "") -> Dict[str, Any]:
    from services.lead_message_playbook_service import suggested_draft_for_lead

    draft = suggested_draft_for_lead(lead, sender_name=sender_name)
    if (note or "").strip() and note.strip() not in (draft.get("text") or ""):
        draft["text"] = (draft.get("text") or "").rstrip() + f"\n\n{note.strip()}"
        draft["wa_url"] = wa_me_url(getattr(lead, "phone", None), draft["text"])
    return draft
