"""Load WhatsApp message stages from docs/onboarding_whatsapp_playbook.md and fill them."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_STAGE_HEADER = re.compile(r"^##\s+([a-z0-9_]+)\s*$", re.M | re.I)
_LABEL = re.compile(r"^\*\*Label:\*\*\s*(.+)\s*$", re.M)
_FENCE = re.compile(r"```(?:text)?\s*\n(.*?)```", re.S)

STAGE_ORDER = [
    "first_contact",
    "followup_1",
    "followup_2",
    "meeting_nudge",
    "risk_invite",
    "proposal_sent_nudge",
    "annual_risk_refresh",
]


def playbook_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    return root / "docs" / "onboarding_whatsapp_playbook.md"


def load_playbook_stages(path: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """Return {stage_key: {label, body}} from the markdown playbook."""
    p = path or playbook_path()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Playbook missing at %s: %s", p, exc)
        return {}

    stages: Dict[str, Dict[str, str]] = {}
    matches = list(_STAGE_HEADER.finditer(text))
    for i, m in enumerate(matches):
        key = m.group(1).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end]
        label_m = _LABEL.search(chunk)
        fence_m = _FENCE.search(chunk)
        body = (fence_m.group(1) if fence_m else "").strip()
        if not body:
            continue
        stages[key] = {
            "key": key,
            "label": (label_m.group(1).strip() if label_m else key.replace("_", " ").title()),
            "body": body,
        }
    return stages


def list_stages(path: Optional[Path] = None) -> List[Dict[str, str]]:
    stages = load_playbook_stages(path)
    ordered = [stages[k] for k in STAGE_ORDER if k in stages]
    for k, meta in stages.items():
        if k not in STAGE_ORDER:
            ordered.append(meta)
    return ordered


def _first_name(lead_or_client) -> str:
    name = (getattr(lead_or_client, "name", None) or "").strip()
    if not name:
        return "there"
    return name.split()[0]


def build_note_snippet(lead, *, max_len: int = 280) -> str:
    """Short context from lead notes / latest call for personalization."""
    bits: List[str] = []
    notes = (getattr(lead, "notes", None) or "").strip()
    if notes:
        # Drop internal markers
        cleaned = re.sub(r"\[.*?\]", "", notes).strip()
        if cleaned:
            bits.append(cleaned[-max_len:])
    try:
        from models import LeadCallLog

        call = (
            LeadCallLog.query.filter_by(lead_id=lead.id)
            .order_by(LeadCallLog.created_at.desc())
            .first()
        )
        if call and (call.notes or "").strip():
            bits.append((call.notes or "").strip()[:160])
    except Exception:
        pass
    if not bits:
        return ""
    snippet = " — ".join(bits)
    if len(snippet) > max_len:
        snippet = snippet[: max_len - 1].rstrip() + "…"
    return f"(Context: {snippet})" if snippet else ""


def fill_template(body: str, mapping: Dict[str, str]) -> str:
    out = body
    for key, val in mapping.items():
        out = out.replace("{" + key + "}", val or "")
    # Drop empty context lines left by blank note_snippet / meeting_hint
    lines = []
    for line in out.splitlines():
        if line.strip() in ("(Context: )", "(Context:)"):
            continue
        lines.append(line)
    # Collapse triple blank lines
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def context_for_lead(
    lead,
    *,
    sender_name: str = "Inertia",
    quiz_url: str = "",
    meeting_hint: str = "",
) -> Dict[str, str]:
    referral = (getattr(lead, "referral_by", None) or "").strip()
    note = build_note_snippet(lead)
    return {
        "first_name": _first_name(lead),
        "lead_name": (getattr(lead, "name", None) or "you").strip() or "you",
        "sender": sender_name or "Inertia",
        "quiz_url": quiz_url or "",
        "note_snippet": note,
        "referral": referral,
        "meeting_hint": meeting_hint or "",
    }


def select_stage_for_lead(lead, *, has_risk: bool = False, has_meeting: bool = False) -> str:
    """Pick suggested playbook stage from lead pipeline state."""
    st = (getattr(lead, "status", None) or "").lower()
    if st in ("proposal_sent", "proposal_reviewed", "agreement_sent"):
        return "proposal_sent_nudge"
    if st in ("risk_profile_sent",) or (not has_risk and st in ("contacted", "qualified", "meeting_done", "meeting_scheduled")):
        return "risk_invite"
    if has_meeting and st in ("new", "contacted", "qualified"):
        return "meeting_nudge"
    if st in ("new", "", None) or st == "open":
        return "first_contact"
    if st in ("contacted",):
        return "followup_1"
    if st in ("qualified", "meeting_scheduled", "meeting_done"):
        return "followup_2"
    if not has_risk:
        return "risk_invite"
    return "followup_1"


def render_stage(
    stage_key: str,
    *,
    lead=None,
    client=None,
    sender_name: str = "Inertia",
    quiz_url: str = "",
    meeting_hint: str = "",
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    stages = load_playbook_stages(path)
    meta = stages.get(stage_key) or stages.get("followup_1") or {}
    subject = lead if lead is not None else client
    mapping = context_for_lead(
        subject,
        sender_name=sender_name,
        quiz_url=quiz_url,
        meeting_hint=meeting_hint,
    ) if subject is not None else {
        "first_name": "there",
        "lead_name": "you",
        "sender": sender_name,
        "quiz_url": quiz_url,
        "note_snippet": "",
        "referral": "",
        "meeting_hint": meeting_hint,
    }
    body = fill_template(meta.get("body") or "", mapping)
    from services.lead_whatsapp_draft_service import wa_me_url

    phone = getattr(subject, "phone", None) if subject is not None else None
    return {
        "kind": stage_key,
        "label": meta.get("label") or stage_key,
        "text": body,
        "wa_url": wa_me_url(phone, body),
        "phone": phone or "",
        "stages": list_stages(path),
        "selected": stage_key,
    }


def suggested_draft_for_lead(
    lead,
    *,
    sender_name: str = "Inertia",
    quiz_url: str = "",
    has_risk: bool = False,
    has_meeting: bool = False,
    stage_key: Optional[str] = None,
) -> Dict[str, Any]:
    key = stage_key or select_stage_for_lead(lead, has_risk=has_risk, has_meeting=has_meeting)
    return render_stage(
        key,
        lead=lead,
        sender_name=sender_name,
        quiz_url=quiz_url,
    )
