"""Persist content intelligence on existing campaign_studio_campaign.payload JSON (no ic_* tables)."""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from extensions import db
from models import CampaignStudioCampaign

IC_KEY = "contentIntelligence"


def _parse_payload(row: CampaignStudioCampaign) -> dict:
    try:
        return json.loads(row.payload) if isinstance(row.payload, str) else (row.payload or {})
    except Exception:
        return {}


def _save_payload(row: CampaignStudioCampaign, payload: dict) -> None:
    row.payload = json.dumps(payload)
    db.session.commit()


def get_campaign_for_user(campaign_id: int, user_id: int) -> Optional[CampaignStudioCampaign]:
    return CampaignStudioCampaign.query.filter_by(id=campaign_id, user_id=user_id).first()


def save_content_intelligence(
    *,
    campaign_id: int,
    user_id: int,
    title: str,
    format: str,
    topic_category: str,
    content_text: str,
    confirmed_tags: Dict[str, bool],
) -> int:
    row = get_campaign_for_user(campaign_id, user_id)
    if not row:
        raise LookupError("campaign not found")
    payload = _parse_payload(row)
    ci = payload.get(IC_KEY) or {}
    ci.update(
        {
            "title": title,
            "format": format,
            "topic_category": topic_category,
            "content_text": (content_text or "")[:50000],
            "confirmed_tags": confirmed_tags,
            "saved_at": date.today().isoformat(),
            "send_log": ci.get("send_log") or [],
        }
    )
    payload[IC_KEY] = ci
    if title:
        row.title = title
    if content_text:
        payload["article"] = content_text
    _save_payload(row, payload)
    return campaign_id


def content_piece_from_campaign(row: CampaignStudioCampaign):
    from inertia_content import ContentPiece

    payload = _parse_payload(row)
    ci = payload.get(IC_KEY) or {}
    tags = ci.get("confirmed_tags") or {}
    piece = ContentPiece(
        id=row.id,
        title=ci.get("title") or row.title,
        format=ci.get("format") or "",
        topic_category=ci.get("topic_category") or "",
        notes=ci.get("content_text") or payload.get("article") or "",
        campaign_studio_campaign_id=row.id,
    )
    for tid, field in (
        ("wealth_accum", "tag_wealth_accum"),
        ("wealth_preserv", "tag_wealth_preserv"),
        ("fin_complexity", "tag_fin_complexity"),
        ("misaligned_prod", "tag_misaligned_prod"),
        ("behav_friction", "tag_behav_friction"),
        ("emotional_inv", "tag_emotional_inv"),
        ("high_awareness", "tag_high_awareness"),
        ("underserved", "tag_underserved"),
        ("life_transition", "tag_life_transition"),
        ("relship_drift", "tag_relship_drift"),
        ("high_earner", "tag_high_earner"),
    ):
        setattr(piece, field, bool(tags.get(tid, False)))
    return piece


def append_send_log(
    *,
    campaign_id: int,
    user_id: int,
    person_id: int,
    person_name: str,
    decision: str,
    channel: str,
) -> int:
    row = get_campaign_for_user(campaign_id, user_id)
    if not row:
        raise LookupError("campaign not found")
    payload = _parse_payload(row)
    ci = payload.get(IC_KEY) or {}
    logs: List[dict] = list(ci.get("send_log") or [])
    log_id = max((int(x.get("log_id", 0)) for x in logs), default=0) + 1
    logs.append(
        {
            "log_id": log_id,
            "date_sent": date.today().isoformat(),
            "person_id": person_id,
            "person_name": person_name,
            "decision": decision,
            "channel": channel,
            "engagement": "None",
            "followup_needed": False,
            "notes": "",
        }
    )
    ci["send_log"] = logs
    payload[IC_KEY] = ci
    _save_payload(row, payload)
    return log_id


def update_send_log_engagement(
    *,
    campaign_id: int,
    user_id: int,
    log_id: int,
    engagement: str,
    followup_needed: bool,
    notes: str,
) -> None:
    row = get_campaign_for_user(campaign_id, user_id)
    if not row:
        raise LookupError("campaign not found")
    payload = _parse_payload(row)
    ci = payload.get(IC_KEY) or {}
    logs: List[dict] = list(ci.get("send_log") or [])
    found = False
    for entry in logs:
        if int(entry.get("log_id", 0)) == log_id:
            entry["engagement"] = engagement
            entry["followup_needed"] = bool(followup_needed)
            entry["notes"] = notes or ""
            found = True
            break
    if not found:
        raise LookupError("log not found")
    ci["send_log"] = logs
    payload[IC_KEY] = ci
    _save_payload(row, payload)
