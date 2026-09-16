"""Build inertia_content Person profiles from Client / Lead rows (no ic_persons table)."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from inertia_content import Person, Category, AgeBracket
from inertia_content.models import AssetBracket, LifeEvent, Milestone, SurplusBracket, Tag
from inertia_content.tags import apply_confirmed_tags

LEAD_ID_OFFSET = 1_000_000_000
IC_TAGS_MARKER = "__IC_ADVISER_TAGS_V1__:"
IC_QUESTIONNAIRE_MARKER = "__IC_QUESTIONNAIRE_V1__:"

QUESTIONNAIRE_BOOL_KEYS = (
    "has_deferred_decision",
    "misses_review_calls",
    "reacted_to_market_event",
    "driven_by_market_news",
    "reads_financial_content",
    "uses_diy_platforms",
    "ca_manages_investments",
    "is_senior_professional",
)

QUESTIONNAIRE_STRING_KEYS = (
    "recent_life_event",
    "upcoming_milestone",
    "age_bracket",
    "investable_assets",
    "monthly_surplus",
    "profession",
    "city",
)

PROFILE_HOLD_KEYS = (
    "holds_mf",
    "holds_stock",
    "holds_lic",
    "holds_ulip",
    "holds_fd",
    "holds_real_estate",
    "holds_pms_aif",
)


def merge_background_notes_edit(existing: Optional[str], new_human_text: Optional[str]) -> Optional[str]:
    """Keep questionnaire JSON when user edits visible background notes on client details."""
    stored = _parse_json_marker(existing, IC_QUESTIONNAIRE_MARKER)
    human = (new_human_text or "").strip()
    base = strip_ic_markers(existing).strip()
    if human:
        base = human
    if stored:
        return _embed_marker(base if base else None, IC_QUESTIONNAIRE_MARKER, stored, None)
    return base or None


def compose_lead_notes(
    human: Optional[str],
    questionnaire: Optional[dict] = None,
    adviser_tags: Optional[dict] = None,
) -> Optional[str]:
    """Human-readable lead notes plus IC JSON markers (both preserved)."""
    parts: List[str] = []
    if human and str(human).strip():
        parts.append(str(human).strip())
    if questionnaire:
        parts.append(IC_QUESTIONNAIRE_MARKER + json.dumps(questionnaire, separators=(",", ":")))
    if adviser_tags:
        parts.append(IC_TAGS_MARKER + json.dumps(adviser_tags, separators=(",", ":")))
    return "\n\n".join(parts) if parts else None


def merge_lead_notes_edit(existing: Optional[str], new_human_text: Optional[str]) -> Optional[str]:
    """Keep IC questionnaire + adviser tags when user edits visible lead notes."""
    stored_q = _parse_json_marker(existing, IC_QUESTIONNAIRE_MARKER)
    stored_tags = _parse_json_marker(existing, IC_TAGS_MARKER)
    human = (new_human_text or "").strip() or strip_ic_markers(existing).strip() or None
    return compose_lead_notes(
        human,
        stored_q if stored_q else None,
        stored_tags if stored_tags else None,
    )


def merge_planning_synopsis_edit(existing: Optional[str], new_human_text: Optional[str]) -> Optional[str]:
    """Keep adviser-tag JSON when user edits visible planning synopsis on client details."""
    stored = _parse_adviser_tags_from_synopsis(existing)
    human = (new_human_text or "").strip()
    base = (existing or "").split(IC_TAGS_MARKER)[0].rstrip() if IC_TAGS_MARKER in (existing or "") else (existing or "").strip()
    if human:
        base = human
    if stored:
        return _embed_marker(base if base else None, IC_TAGS_MARKER, stored, None)
    return base or None


def strip_ic_markers(text: Optional[str]) -> str:
    """Human-readable notes without embedded IC JSON blobs."""
    if not text:
        return ""
    out = text
    for marker in (IC_TAGS_MARKER, IC_QUESTIONNAIRE_MARKER):
        if marker in out:
            out = out.split(marker)[0]
    return out.rstrip()


def _parse_json_marker(text: Optional[str], marker: str) -> dict:
    if not text or marker not in text:
        return {}
    try:
        chunk = text.split(marker, 1)[1].strip().split("\n", 1)[0]
        data = json.loads(chunk)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _embed_marker(existing: Optional[str], marker: str, data: dict, other_marker: Optional[str] = None) -> str:
    base = (existing or "")
    if marker in base:
        base = base.split(marker)[0]
    if other_marker and other_marker in base:
        base = base.split(other_marker)[0]
    base = base.rstrip()
    blob = marker + json.dumps(data, separators=(",", ":"))
    return (base + "\n\n" + blob).strip() if base else blob


def _parse_adviser_tags_from_synopsis(text: Optional[str]) -> dict:
    data = _parse_json_marker(text, IC_TAGS_MARKER)
    return {k: bool(v) for k, v in data.items() if k in Tag.ADVISER}


def _parse_questionnaire_from_notes(text: Optional[str]) -> dict:
    return _parse_json_marker(text, IC_QUESTIONNAIRE_MARKER)


def _age_bracket_from_dob(dob: Optional[date]) -> str:
    if not dob:
        return ""
    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if age < 35:
        return AgeBracket.A25_34
    if age < 45:
        return AgeBracket.A35_44
    if age < 55:
        return AgeBracket.A45_54
    if age < 65:
        return AgeBracket.A55_64
    return AgeBracket.A65P


def _asset_bracket_from_inr(amount: float) -> str:
    if amount <= 0:
        return ""
    if amount < 1_000_000:
        return AssetBracket.LT10L
    if amount < 2_500_000:
        return AssetBracket.L10_25
    if amount < 5_000_000:
        return AssetBracket.L25_50
    if amount < 10_000_000:
        return AssetBracket.L50_1CR
    if amount < 20_000_000:
        return AssetBracket.L1_2CR
    return AssetBracket.GT2CR


def _surplus_bracket_from_inr(monthly: float) -> str:
    if monthly <= 0:
        return ""
    if monthly < 25_000:
        return SurplusBracket.LT25K
    if monthly < 50_000:
        return SurplusBracket.K25_50
    if monthly < 100_000:
        return SurplusBracket.K50_1L
    if monthly < 200_000:
        return SurplusBracket.L1_2
    if monthly < 300_000:
        return SurplusBracket.L2_3
    return SurplusBracket.GT3L


def _city_from_address(address: Optional[str]) -> str:
    if not address:
        return ""
    parts = [p.strip() for p in address.replace("\n", ",").split(",") if p.strip()]
    return parts[-1][:80] if parts else address[:80]


def _apply_holding_row_flags(person: Person, *, asset_class: str, security_type: str = "") -> None:
    """Set holds_* flags from asset class / type labels (holdings table or snapshot)."""
    from utils.portfolio_asset_class_display import is_mutual_fund_security_type

    ac = (asset_class or "").lower()
    st = (security_type or "").lower()
    if is_mutual_fund_security_type(security_type) or "mutual" in ac:
        person.holds_mf = True
    if "reit" in ac or "real estate" in ac:
        person.holds_real_estate = True
    if "pms" in ac or "aif" in ac:
        person.holds_pms_aif = True
    if "lic" in ac or (st == "insurance" and "ulip" not in ac):
        person.holds_lic = True
    if "ulip" in ac or "ulip" in st:
        person.holds_ulip = True
    if "fd" in ac or "fixed deposit" in ac:
        person.holds_fd = True
    if ("equity" in ac or st in ("stock", "equity")) and "mutual" not in ac:
        person.holds_stock = True


def _apply_holdings(person: Person, client_id: int) -> None:
    from models import Holding

    rows = (
        Holding.query.filter_by(client_id=client_id)
        .filter(Holding.quantity > 0)
        .all()
    )
    for h in rows:
        sec = h.security
        if not sec:
            continue
        ac = sec.asset_class.name if sec.asset_class else ""
        _apply_holding_row_flags(person, asset_class=ac, security_type=sec.security_type or "")


def _holdings_table_total_value(client_id: int) -> float:
    """Sum qty × stored security price from holdings table — no forward reconstruction."""
    from models import Holding, Security

    total = 0.0
    rows = (
        Holding.query.filter_by(client_id=client_id)
        .filter(Holding.quantity > 0)
        .all()
    )
    for h in rows:
        qty = float(h.quantity or 0)
        if qty <= 0:
            continue
        sec = Security.query.get(h.security_id)
        price = 0.0
        if sec and sec.current_price:
            price = float(sec.current_price)
        elif h.average_price:
            price = float(h.average_price)
        total += qty * price
    return total


def _apply_holdings_from_snapshot(person: Person, holdings_snapshot: Dict[int, dict]) -> float:
    """Use holdings already computed for client details (same as page table)."""
    total_value = 0.0
    for h in holdings_snapshot.values():
        total_value += float(h.get("value") or 0)
        _apply_holding_row_flags(
            person,
            asset_class=str(h.get("asset_class") or ""),
            security_type="",
        )
    return total_value


def apply_profile_holdings(
    person: Person,
    client_id: int,
    holdings_snapshot: Optional[Dict[int, dict]] = None,
) -> float:
    """
    Profile UI: holdings table / page snapshot only — never forward_holding_calculation_service.
    Returns total market value used for asset bracket hint.
    """
    if holdings_snapshot:
        total = _apply_holdings_from_snapshot(person, holdings_snapshot)
    else:
        _apply_holdings(person, client_id)
        total = _holdings_table_total_value(client_id)
    if total > 0:
        bracket = _asset_bracket_from_inr(total)
        if bracket and not person.investable_assets:
            person.investable_assets = bracket
    return total


def _last_contact_for_client(client_id: int) -> Optional[date]:
    from models import CallLog, Meeting

    latest: Optional[date] = None
    last_call = (
        CallLog.query.filter_by(client_id=client_id)
        .order_by(CallLog.call_date.desc())
        .first()
    )
    if last_call and last_call.call_date:
        latest = last_call.call_date.date() if isinstance(last_call.call_date, datetime) else last_call.call_date
    last_meeting = (
        Meeting.query.filter_by(client_id=client_id)
        .order_by(Meeting.meeting_date.desc())
        .first()
    )
    if last_meeting and last_meeting.meeting_date:
        md = (
            last_meeting.meeting_date.date()
            if isinstance(last_meeting.meeting_date, datetime)
            else last_meeting.meeting_date
        )
        if latest is None or md > latest:
            latest = md
    return latest


def _last_contact_for_lead(lead_id: int) -> Optional[date]:
    from models import LeadCallLog, Meeting

    latest: Optional[date] = None
    last_call = (
        LeadCallLog.query.filter_by(lead_id=lead_id)
        .order_by(LeadCallLog.call_date.desc())
        .first()
    )
    if last_call and last_call.call_date:
        latest = last_call.call_date.date() if isinstance(last_call.call_date, datetime) else last_call.call_date
    last_meeting = (
        Meeting.query.filter_by(lead_id=lead_id)
        .order_by(Meeting.meeting_date.desc())
        .first()
    )
    if last_meeting and last_meeting.meeting_date:
        md = (
            last_meeting.meeting_date.date()
            if isinstance(last_meeting.meeting_date, datetime)
            else last_meeting.meeting_date
        )
        if latest is None or md > latest:
            latest = md
    return latest


def _infer_misses_review_calls(client_id: int) -> bool:
    from models import ReviewWorkflow

    today = date.today()
    stale = (
        ReviewWorkflow.query.filter_by(client_id=client_id)
        .filter(ReviewWorkflow.status.in_(("initiated", "sent")))
        .filter(ReviewWorkflow.review_date < today)
        .count()
    )
    return stale >= 2


def _apply_questionnaire_to_person(person: Person, q: dict) -> None:
    for key in QUESTIONNAIRE_BOOL_KEYS:
        if key in q:
            setattr(person, key, bool(q[key]))
    for key in QUESTIONNAIRE_STRING_KEYS:
        if key in q:
            setattr(person, key, str(q[key] or ""))
    for key in PROFILE_HOLD_KEYS:
        if key in q:
            setattr(person, key, bool(q[key]))
    if "is_nri" in q:
        person.is_nri = bool(q["is_nri"])
    if "is_business_owner" in q:
        person.is_business_owner = bool(q["is_business_owner"])
    if "is_cross_border" in q:
        person.is_cross_border = bool(q["is_cross_border"])
    if "engagement_score" in q:
        try:
            person.engagement_score = max(1, min(5, int(q["engagement_score"])))
        except (TypeError, ValueError):
            pass


def _questionnaire_from_person(person: Person) -> dict:
    out = {k: bool(getattr(person, k)) for k in QUESTIONNAIRE_BOOL_KEYS}
    out["recent_life_event"] = person.recent_life_event or LifeEvent.NONE
    out["upcoming_milestone"] = person.upcoming_milestone or Milestone.NONE
    out["is_nri"] = bool(person.is_nri)
    out["is_business_owner"] = bool(person.is_business_owner)
    out["is_cross_border"] = bool(person.is_cross_border)
    out["age_bracket"] = person.age_bracket or ""
    out["investable_assets"] = person.investable_assets or ""
    out["monthly_surplus"] = person.monthly_surplus or ""
    out["profession"] = person.profession or ""
    out["city"] = person.city or ""
    out["engagement_score"] = person.engagement_score or 0
    for key in PROFILE_HOLD_KEYS:
        out[key] = bool(getattr(person, key))
    return out


def _apply_static_client_hints(person: Person, client) -> None:
    """Client row + notes only — no portfolio valuation or holdings scans."""
    des = (client.designation or "").lower()
    if not person.is_senior_professional and des:
        person.is_senior_professional = any(
            x in des for x in ("ceo", "cfo", "cto", "founder", "partner", "director", "vp", "president")
        )
    if not person.prior_adviser:
        person.prior_adviser = bool(client.portfolio_inherited)
    notes_l = ((client.background_notes or "") + (client.other_notes or "")).lower()
    if not person.is_nri and ("nri" in notes_l or "non-resident" in notes_l):
        person.is_nri = True
    if client.monthly_investment_schedule and client.monthly_investment_schedule.is_active:
        amt = float(client.monthly_investment_schedule.planned_amount or 0)
        if amt and not person.monthly_surplus:
            person.monthly_surplus = _surplus_bracket_from_inr(amt)
    if client.starting_aua and not person.investable_assets:
        person.investable_assets = _asset_bracket_from_inr(float(client.starting_aua))
    if not person.engagement_score:
        person.engagement_score = 3 if client.is_active else 1


def _infer_questionnaire_defaults(person: Person, client) -> None:
    """Fill questionnaire booleans only when not already stored in notes blob."""
    if not person.misses_review_calls:
        person.misses_review_calls = _infer_misses_review_calls(client.id)
    _apply_static_client_hints(person, client)


def client_to_person(
    client,
    *,
    full: bool = True,
    profile_static: bool = False,
    holdings_snapshot: Optional[Dict[int, dict]] = None,
) -> Person:
    category = Category.CLIENT if client.is_active else Category.INACTIVE_CLIENT
    stored_tags = _parse_adviser_tags_from_synopsis(client.planning_synopsis)
    stored_q = _parse_questionnaire_from_notes(client.background_notes)

    p = Person(
        id=client.id,
        name=client.name or "",
        category=category,
        profession=client.industry or client.designation or "",
        age_bracket=_age_bracket_from_dob(client.date_of_birth),
        city=_city_from_address(client.address) or "",
        onboarded_date=client.date_of_joining,
        prior_adviser=bool(client.portfolio_inherited),
        notes=strip_ic_markers(client.background_notes or client.other_notes or ""),
        tag_behav_friction=stored_tags.get(Tag.BEHAV_FRICTION, False),
        tag_emotional_inv=stored_tags.get(Tag.EMOTIONAL_INV, False),
        tag_high_awareness=stored_tags.get(Tag.HIGH_AWARENESS, False),
        tag_underserved=stored_tags.get(Tag.UNDERSERVED, False),
        tag_life_transition=stored_tags.get(Tag.LIFE_TRANSITION, False),
        tag_relship_drift=stored_tags.get(Tag.RELSHIP_DRIFT, False),
        tag_high_earner=stored_tags.get(Tag.HIGH_EARNER, False),
    )

    if profile_static:
        _apply_questionnaire_to_person(p, stored_q)
        if not stored_q:
            _apply_static_client_hints(p, client)
        holdings_total = apply_profile_holdings(
            p, client.id, holdings_snapshot=holdings_snapshot
        )
        if holdings_total:
            setattr(p, "_ic_holdings_total_inr", holdings_total)
    elif full:
        _apply_questionnaire_to_person(p, stored_q)
        if not stored_q:
            _infer_questionnaire_defaults(p, client)
        p.last_contacted = _last_contact_for_client(client.id)
        if client.monthly_investment_schedule and client.monthly_investment_schedule.is_active:
            amt = float(client.monthly_investment_schedule.planned_amount or 0)
            p.monthly_surplus = _surplus_bracket_from_inr(amt)
        try:
            from services.portfolio_valuation_service import PortfolioValuationService

            total = PortfolioValuationService.total_market_value_for_client(client.id)
            bracket = _asset_bracket_from_inr(total)
            if bracket:
                p.investable_assets = bracket
            elif client.starting_aua:
                p.investable_assets = _asset_bracket_from_inr(float(client.starting_aua))
        except Exception:
            if client.starting_aua:
                p.investable_assets = _asset_bracket_from_inr(float(client.starting_aua))
        _apply_holdings(p, client.id)
        if not p.engagement_score:
            p.engagement_score = 3 if client.is_active else 1

    return p


def lead_to_person(lead, *, full: bool = True, profile_static: bool = False) -> Person:
    stored_q = _parse_questionnaire_from_notes(lead.notes)
    stored_tags = _parse_json_marker(lead.notes, IC_TAGS_MARKER)
    p = Person(
        id=lead.id + LEAD_ID_OFFSET,
        name=lead.name or "",
        category=Category.LEAD,
        notes=strip_ic_markers(lead.notes or ""),
        engagement_score=int(lead.temperature or 3),
    )
    if profile_static or full:
        _apply_questionnaire_to_person(p, stored_q)
        if stored_tags:
            apply_confirmed_tags(
                p,
                {tid: bool(stored_tags[tid]) for tid in Tag.ADVISER if tid in stored_tags},
            )
        if full and not profile_static:
            p.last_contacted = _last_contact_for_lead(lead.id)
    return p


def list_persons_for_matching() -> List[Person]:
    from models import Client, Lead

    persons: List[Person] = []
    for c in Client.query.filter(Client.is_active.is_(True)).limit(500).all():
        persons.append(client_to_person(c))
    for lead in Lead.query.filter(Lead.is_active.is_(True)).limit(200).all():
        persons.append(lead_to_person(lead))
    return persons


def get_person_by_id(
    person_id: int,
    *,
    profile_static: bool = False,
    holdings_snapshot: Optional[Dict[int, dict]] = None,
) -> Optional[Person]:
    """profile_static=True: questionnaire + holdings table (no forward calc)."""
    from models import Client, Lead

    if person_id >= LEAD_ID_OFFSET:
        lead = Lead.query.get(person_id - LEAD_ID_OFFSET)
        if not lead:
            return None
        return lead_to_person(lead, full=not profile_static, profile_static=profile_static)
    client = Client.query.get(person_id)
    if not client:
        return None
    return client_to_person(
        client,
        full=not profile_static,
        profile_static=profile_static,
        holdings_snapshot=holdings_snapshot,
    )


def person_display_name(person_id: int) -> str:
    p = get_person_by_id(person_id)
    return p.name if p else ""


def person_profile_readonly(person: Person) -> Dict[str, Any]:
    out = {
        "age_bracket": person.age_bracket,
        "profession": person.profession,
        "city": person.city,
        "category": person.category,
        "investable_assets": person.investable_assets,
        "monthly_surplus": person.monthly_surplus,
        "holds_mf": person.holds_mf,
        "holds_stock": person.holds_stock,
        "holds_lic": person.holds_lic,
        "holds_ulip": person.holds_ulip,
        "holds_fd": person.holds_fd,
        "holds_real_estate": person.holds_real_estate,
        "holds_pms_aif": person.holds_pms_aif,
        "last_contacted": person.last_contacted.isoformat() if person.last_contacted else None,
        "engagement_score": person.engagement_score,
        "holdings_source": "holdings_table",
    }
    total = getattr(person, "_ic_holdings_total_inr", None)
    if total:
        out["total_value_inr"] = float(total)
    return out


def save_person_profile(person_id: int, body: dict) -> None:
    """Persist questionnaire (background_notes / lead.notes) and adviser tags (planning_synopsis)."""
    from extensions import db
    from models import Client, Lead

    questionnaire = body.get("questionnaire") or {}
    adviser = body.get("adviser_tags") or body

    if person_id >= LEAD_ID_OFFSET:
        lead = Lead.query.get(person_id - LEAD_ID_OFFSET)
        if not lead:
            raise LookupError("person not found")
        q_blob = {k: questionnaire[k] for k in QUESTIONNAIRE_BOOL_KEYS if k in questionnaire}
        for k in QUESTIONNAIRE_STRING_KEYS:
            if k in questionnaire:
                q_blob[k] = questionnaire[k]
        for k in PROFILE_HOLD_KEYS:
            if k in questionnaire:
                q_blob[k] = bool(questionnaire[k])
        for k in ("is_nri", "is_business_owner", "is_cross_border"):
            if k in questionnaire:
                q_blob[k] = bool(questionnaire[k])
        if "engagement_score" in questionnaire:
            q_blob["engagement_score"] = int(questionnaire["engagement_score"] or 0)

        person = lead_to_person(lead, full=False, profile_static=True)
        confirmed = {}
        for tid in Tag.ADVISER:
            key = f"tag_{tid}"
            if key in adviser:
                confirmed[tid] = bool(adviser[key])
            elif tid in adviser:
                confirmed[tid] = bool(adviser[tid])
        apply_confirmed_tags(person, confirmed)
        tag_blob = {tid: bool(getattr(person, f"tag_{tid}")) for tid in Tag.ADVISER}
        human = strip_ic_markers(lead.notes).strip() or None
        lead.notes = compose_lead_notes(human, q_blob, tag_blob)
        db.session.commit()
        return

    client = Client.query.get(person_id)
    if not client:
        raise LookupError("person not found")

    person = client_to_person(client)
    q_blob = _questionnaire_from_person(person)
    for key in QUESTIONNAIRE_BOOL_KEYS:
        if key in questionnaire:
            q_blob[key] = bool(questionnaire[key])
    for key in QUESTIONNAIRE_STRING_KEYS:
        if key in questionnaire:
            q_blob[key] = str(questionnaire[key] or "")
    for key in PROFILE_HOLD_KEYS:
        if key in questionnaire:
            q_blob[key] = bool(questionnaire[key])
    for key in ("is_nri", "is_business_owner", "is_cross_border"):
        if key in questionnaire:
            q_blob[key] = bool(questionnaire[key])
    if "engagement_score" in questionnaire:
        try:
            q_blob["engagement_score"] = max(0, min(5, int(questionnaire["engagement_score"])))
        except (TypeError, ValueError):
            pass
    client.background_notes = _embed_marker(
        client.background_notes, IC_QUESTIONNAIRE_MARKER, q_blob, IC_TAGS_MARKER
    )

    confirmed = {}
    for tid in Tag.ADVISER:
        key = f"tag_{tid}"
        if key in adviser:
            confirmed[tid] = bool(adviser[key])
        elif tid in adviser:
            confirmed[tid] = bool(adviser[tid])
    apply_confirmed_tags(person, confirmed)
    tag_blob = {tid: bool(getattr(person, f"tag_{tid}")) for tid in Tag.ADVISER}
    client.planning_synopsis = _embed_marker(
        client.planning_synopsis, IC_TAGS_MARKER, tag_blob, None
    )
    db.session.commit()


def save_person_adviser_tags(person_id: int, body: dict) -> None:
    save_person_profile(person_id, {"adviser_tags": body})
