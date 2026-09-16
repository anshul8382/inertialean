"""Flask orchestration for inertia_content — no ic_* DDL; uses campaign payload + Client/Lead."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask_login import current_user

from inertia_content import Tag
from inertia_content.matcher import rank_persons_for_content
from inertia_content.models import AgeBracket, AssetBracket, LifeEvent, Milestone, SurplusBracket
from inertia_content.tags import suggest_adviser_tags
from inertia_content.tagger import suggest_tags
from services.anthropic_client_service import get_anthropic_client
from services import content_intelligence_payload as payload_store
from services import content_intelligence_person_source as person_source


def generate_adviser_tag_suggestions(content_text: str) -> Dict[str, Any]:
    if not (content_text or "").strip():
        raise ValueError("content is required")
    client = get_anthropic_client()
    result = suggest_tags(content_text, anthropic_client=client)
    tags_out = {tid: bool(result.tags.get(tid, False)) for tid in Tag.ADVISER}
    reasons_out = {tid: str(result.reasons.get(tid, "")) for tid in Tag.ADVISER}
    out: Dict[str, Any] = {"tags": tags_out, "reasons": reasons_out}
    if result.provider == "ollama":
        out["_llm_provider"] = "ollama"
    return out


def _tags_from_confirmed(confirmed: Dict[str, bool]) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for tid in Tag.ALL:
        if tid in confirmed:
            out[tid] = bool(confirmed[tid])
        elif f"tag_{tid}" in confirmed:
            out[tid] = bool(confirmed[f"tag_{tid}"])
    return out


def save_content_with_tags(
    *,
    title: str,
    format: str,
    topic_category: str,
    content_text: str,
    confirmed_tags: Dict[str, bool],
    campaign_studio_campaign_id: Optional[int] = None,
) -> int:
    if campaign_studio_campaign_id is None:
        raise ValueError("campaign_studio_campaign_id is required (tags stored in campaign payload JSON)")
    tags = _tags_from_confirmed(confirmed_tags)
    api_tags = {tid: tags.get(tid, False) for tid in Tag.ALL}
    return payload_store.save_content_intelligence(
        campaign_id=int(campaign_studio_campaign_id),
        user_id=current_user.id,
        title=title,
        format=format,
        topic_category=topic_category,
        content_text=content_text,
        confirmed_tags=api_tags,
    )


def match_persons_for_content(content_id: int) -> List[Dict[str, Any]]:
    """content_id is campaign_studio_campaign.id; matching uses payload tags + Client/Lead."""
    row = payload_store.get_campaign_for_user(content_id, current_user.id)
    if not row:
        raise LookupError("content not found")
    piece = payload_store.content_piece_from_campaign(row)
    ci = payload_store._parse_payload(row).get(payload_store.IC_KEY) or {}
    if not ci.get("confirmed_tags"):
        raise LookupError("no confirmed tags on this campaign — save content first")

    persons = person_source.list_persons_for_matching()
    results = rank_persons_for_content(persons, piece, min_match=1)
    out = []
    for r in results:
        pid = r.person.id
        is_lead = pid >= person_source.LEAD_ID_OFFSET
        out.append(
            {
                "person_id": pid,
                "name": r.person.name,
                "category": r.person.category,
                "match_count": r.match_count,
                "matched_tags": [Tag.LABELS.get(t, t) for t in r.matched_tags],
                "days_since_contact": r.days_since_contact,
                "ok_to_contact": r.ok_to_contact,
                "contact_reason": r.contact_reason,
                "client_id": None if is_lead else pid,
                "lead_id": (pid - person_source.LEAD_ID_OFFSET) if is_lead else None,
            }
        )
    return out


def log_send_decision(
    *,
    content_id: int,
    person_id: int,
    decision: str,
    channel: str,
) -> int:
    name = person_source.person_display_name(person_id)
    if not name:
        raise LookupError("person not found")
    return payload_store.append_send_log(
        campaign_id=content_id,
        user_id=current_user.id,
        person_id=person_id,
        person_name=name,
        decision=decision,
        channel=channel,
    )


def update_engagement_feedback(
    log_id: int,
    *,
    engagement: str,
    followup_needed: bool,
    notes: str = "",
    campaign_id: Optional[int] = None,
) -> None:
    if campaign_id is None:
        raise ValueError("campaign_id required in body when using payload send log")
    payload_store.update_send_log_engagement(
        campaign_id=int(campaign_id),
        user_id=current_user.id,
        log_id=log_id,
        engagement=engagement,
        followup_needed=followup_needed,
        notes=notes,
    )


def person_suggest_tags_json(person_id: int, *, profile_static: bool = True) -> List[Dict[str, Any]]:
    person = person_source.get_person_by_id(person_id, profile_static=profile_static)
    if not person:
        raise LookupError("person not found")
    return [_suggestion_to_api(s) for s in suggest_adviser_tags(person)]


def person_confirm_tags(person_id: int, body: Dict[str, Any]) -> None:
    person_source.save_person_adviser_tags(person_id, body)


def _profile_options() -> Dict[str, Any]:
    return {
        "age_brackets": AgeBracket.ALL,
        "asset_brackets": AssetBracket.ALL,
        "surplus_brackets": SurplusBracket.ALL,
        "engagement_scores": [0, 1, 2, 3, 4, 5],
    }


def person_content_profile_from_person(person, *, person_id: int, is_lead: bool) -> Dict[str, Any]:
    """Build profile payload from an in-memory Person (no extra DB round-trip)."""
    adviser_tags = {tid: bool(getattr(person, f"tag_{tid}")) for tid in Tag.ADVISER}
    try:
        tag_suggestions = [_suggestion_to_api(s) for s in suggest_adviser_tags(person)]
    except Exception:
        tag_suggestions = []
    readonly = person_source.person_profile_readonly(person)
    return {
        "person_id": person_id,
        "name": person.name,
        "category": person.category,
        "is_lead": is_lead,
        "readonly": readonly,
        "questionnaire": person_source._questionnaire_from_person(person),
        "adviser_tags": adviser_tags,
        "tag_suggestions": tag_suggestions,
        "life_events": LifeEvent.ALL,
        "milestones": Milestone.ALL,
        "options": _profile_options(),
        "system_snapshot": readonly,
    }


def person_content_profile_seed_for_client(
    client,
    holdings_snapshot: Optional[Dict[int, dict]] = None,
) -> Dict[str, Any]:
    """Seed for client details embed — uses page holdings snapshot when provided."""
    person = person_source.client_to_person(
        client, profile_static=True, holdings_snapshot=holdings_snapshot
    )
    return person_content_profile_from_person(person, person_id=client.id, is_lead=False)


def person_content_profile(person_id: int) -> Dict[str, Any]:
    # UI profile: no live holdings/pricing forward calc — static + saved questionnaire only.
    person = person_source.get_person_by_id(person_id, profile_static=True)
    if not person:
        raise LookupError("person not found")
    is_lead = person_id >= person_source.LEAD_ID_OFFSET
    return person_content_profile_from_person(person, person_id=person_id, is_lead=is_lead)


def save_person_content_profile(person_id: int, body: Dict[str, Any]) -> None:
    person_source.save_person_profile(person_id, body)


def get_campaign_send_log(campaign_id: int) -> List[Dict[str, Any]]:
    row = payload_store.get_campaign_for_user(campaign_id, current_user.id)
    if not row:
        raise LookupError("campaign not found")
    ci = payload_store._parse_payload(row).get(payload_store.IC_KEY) or {}
    return list(ci.get("send_log") or [])


def _suggestion_to_api(s) -> Dict[str, Any]:
    return {
        "tag": s.tag,
        "label": Tag.LABELS.get(s.tag, s.tag),
        "suggested": s.suggested,
        "confidence": s.confidence,
        "signals": list(s.signals),
        "reason": s.reason,
    }
