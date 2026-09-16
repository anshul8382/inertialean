"""
Tag inference engine for Inertia Content Intelligence.

Auto tags — pure formula from profile fields, computed fresh each time.
Adviser tags — rule-based suggestions from questionnaire; adviser confirms once per year.
"""

from datetime import date

from .models import (
    Person,
    Tag,
    TagSuggestionResult,
    Confidence,
    AgeBracket,
    AssetBracket,
    SurplusBracket,
    Category,
    LifeEvent,
    Milestone,
)


def infer_wealth_accum(p: Person) -> bool:
    return (
        p.age_bracket in [AgeBracket.A25_34, AgeBracket.A35_44]
        and p.category != Category.INACTIVE_CLIENT
    )


def infer_wealth_preserv(p: Person) -> bool:
    return p.age_bracket in [AgeBracket.A45_54, AgeBracket.A55_64, AgeBracket.A65P]


def infer_fin_complexity(p: Person) -> bool:
    return any([
        p.is_nri,
        p.is_business_owner,
        p.is_cross_border,
        p.investable_assets == AssetBracket.GT2CR,
    ])


def infer_misaligned_prod(p: Person) -> bool:
    return any([p.holds_lic, p.holds_ulip, p.holds_fd])


def _suggest_behav_friction(p: Person) -> TagSuggestionResult:
    signals = []
    if p.has_deferred_decision:
        signals.append("Has deferred a financial decision 2+ times")
    if p.misses_review_calls:
        signals.append("Missed 2+ scheduled review calls")
    if p.category == Category.INACTIVE_CLIENT:
        signals.append("Currently inactive client — pattern of disengagement")
    if p.last_contacted:
        days = (date.today() - p.last_contacted).days
        if days > 180:
            signals.append(f"No contact in {days} days — long-standing inaction")

    count = len(signals)
    return TagSuggestionResult(
        tag=Tag.BEHAV_FRICTION,
        suggested=count >= 1,
        confidence=Confidence.HIGH if count >= 2 else Confidence.MEDIUM if count == 1 else Confidence.LOW,
        signals=signals,
        reason=(
            "Multiple deferral signals detected — likely stuck in inaction loop"
            if count >= 2
            else "One deferral signal detected — worth probing in next conversation"
            if count == 1
            else "No deferral signals from questionnaire — adviser to assess from conversations"
        ),
    )


def _suggest_emotional_inv(p: Person) -> TagSuggestionResult:
    signals = []
    if p.reacted_to_market_event:
        signals.append("Has reacted emotionally to a market event — panic or euphoria")
    if p.driven_by_market_news:
        signals.append("Frequently asks about news-driven moves")
    if p.engagement_score <= 2 and p.category == Category.INACTIVE_CLIENT:
        signals.append("Low engagement + inactive — may have disengaged after a market disappointment")

    count = len(signals)
    return TagSuggestionResult(
        tag=Tag.EMOTIONAL_INV,
        suggested=count >= 1,
        confidence=Confidence.HIGH if count >= 2 else Confidence.MEDIUM if count == 1 else Confidence.LOW,
        signals=signals,
        reason=(
            "Clear emotional investing pattern — reacts to market noise"
            if count >= 2
            else "One emotional signal — observe in next market move"
            if count == 1
            else "No emotional signals from questionnaire — adviser to assess from conversations"
        ),
    )


def _suggest_high_awareness(p: Person) -> TagSuggestionResult:
    signals = []
    if p.reads_financial_content:
        signals.append("Reads financial content — Freefincal, blogs, Zerodha Varsity etc.")
    if p.uses_diy_platforms:
        signals.append("Uses DIY platforms — Zerodha, Smallcase, Groww directly")
    if p.profession in ["Technology", "Finance / Banking", "Legal"]:
        signals.append(f"Profession ({p.profession}) correlates with high financial literacy")
    if p.engagement_score >= 4:
        signals.append("High engagement score — asks good questions, follows through")

    count = len(signals)
    return TagSuggestionResult(
        tag=Tag.HIGH_AWARENESS,
        suggested=count >= 1,
        confidence=Confidence.HIGH if count >= 2 else Confidence.MEDIUM if count == 1 else Confidence.LOW,
        signals=signals,
        reason=(
            "Strong high-awareness profile — reads widely, may be DIY-inclined"
            if count >= 2
            else "Some awareness signals — content should respect their knowledge level"
            if count == 1
            else "No awareness signals — adviser to assess from conversations"
        ),
    )


def _suggest_underserved(p: Person) -> TagSuggestionResult:
    signals = []
    if p.has_ca_rm_mfd:
        signals.append("Has CA / bank RM / MFD relationship")
    if p.ca_manages_investments:
        signals.append("CA or RM actively 'handles' their investments — role confusion")
    if infer_misaligned_prod(p) and p.has_ca_rm_mfd:
        signals.append("Holds LIC/ULIP/FD + has CA/MFD — likely mis-sold through that relationship")
    if p.prior_adviser and infer_misaligned_prod(p):
        signals.append("Had prior adviser but still holds misaligned products")

    count = len(signals)
    return TagSuggestionResult(
        tag=Tag.UNDERSERVED,
        suggested=count >= 1,
        confidence=Confidence.HIGH if count >= 2 else Confidence.MEDIUM if count == 1 else Confidence.LOW,
        signals=signals,
        reason=(
            "Clear underserved pattern — has an adviser relationship but wrong products"
            if count >= 2
            else "Possible underserved situation — worth exploring who currently advises them"
            if count == 1
            else "No underserved signals detected"
        ),
    )


def _suggest_life_transition(p: Person) -> TagSuggestionResult:
    signals = []
    if p.recent_life_event != LifeEvent.NONE:
        signals.append(f"Recent life event: {p.recent_life_event}")
    if p.upcoming_milestone != Milestone.NONE:
        signals.append(f"Upcoming milestone: {p.upcoming_milestone}")
    if p.is_nri and p.recent_life_event == LifeEvent.NRI_RETURN:
        signals.append("NRI returning to India — major financial restructuring needed")
    if p.is_business_owner and p.recent_life_event == LifeEvent.BUSINESS_EXIT:
        signals.append("Business exit event — liquidity and reinvestment decisions pending")

    count = len(signals)
    return TagSuggestionResult(
        tag=Tag.LIFE_TRANSITION,
        suggested=count >= 1,
        confidence=Confidence.HIGH if count >= 2 else Confidence.MEDIUM if count == 1 else Confidence.LOW,
        signals=signals,
        reason=(
            "Active transition period — multiple life events in play"
            if count >= 2
            else f"Life transition detected: {signals[0]}"
            if count == 1
            else "No life transition signals — stable life stage"
        ),
    )


def _suggest_relship_drift(p: Person) -> TagSuggestionResult:
    signals = []
    if p.category == Category.INACTIVE_CLIENT:
        signals.append("Categorised as Inactive Client")
    if p.last_contacted:
        days = (date.today() - p.last_contacted).days
        if days > 180:
            signals.append(f"Not contacted in {days} days")
        elif days > 90:
            signals.append(f"Last contact was {days} days ago — cooling off")
    if not p.last_contacted and p.category in [Category.LEAD, Category.PROSPECT]:
        signals.append("Lead or prospect with no contact date recorded")
    if p.misses_review_calls:
        signals.append("Regularly misses review calls")
    if p.engagement_score <= 1:
        signals.append("Very low engagement score (1/5)")

    count = len(signals)
    return TagSuggestionResult(
        tag=Tag.RELSHIP_DRIFT,
        suggested=count >= 1,
        confidence=Confidence.HIGH if count >= 2 else Confidence.MEDIUM if count == 1 else Confidence.LOW,
        signals=signals,
        reason=(
            "Strong drift signals — relationship has clearly lost momentum"
            if count >= 2
            else "Early drift signal — worth a proactive check-in soon"
            if count == 1
            else "No drift signals — relationship appears active"
        ),
    )


def _suggest_high_earner(p: Person) -> TagSuggestionResult:
    signals = []
    if p.investable_assets == AssetBracket.GT2CR:
        signals.append("Investable assets > 2 Cr")
    if p.monthly_surplus in [SurplusBracket.L2_3, SurplusBracket.GT3L]:
        signals.append(f"Monthly surplus: {p.monthly_surplus}")
    if p.is_senior_professional:
        signals.append("Senior professional — CXO, partner, or high-billing role")
    if p.is_business_owner and p.investable_assets in [AssetBracket.L1_2CR, AssetBracket.GT2CR]:
        signals.append("Business owner with significant assets")
    if p.holds_pms_aif:
        signals.append("Holds PMS/AIF — typically requires 50L+ minimum")

    count = len(signals)
    return TagSuggestionResult(
        tag=Tag.HIGH_EARNER,
        suggested=count >= 1,
        confidence=Confidence.HIGH if count >= 2 else Confidence.MEDIUM if count == 1 else Confidence.LOW,
        signals=signals,
        reason=(
            "Clear high-earner profile — assets and surplus both strong"
            if count >= 2
            else "One high-earner signal — confirm with adviser judgement"
            if count == 1
            else "No high-earner signals from profile — may still apply based on profession"
        ),
    )


def suggest_adviser_tags(p: Person) -> list:
    """Rule-based suggestions for all 7 adviser tags — no external calls."""
    return [
        _suggest_behav_friction(p),
        _suggest_emotional_inv(p),
        _suggest_high_awareness(p),
        _suggest_underserved(p),
        _suggest_life_transition(p),
        _suggest_relship_drift(p),
        _suggest_high_earner(p),
    ]


def apply_confirmed_tags(p: Person, confirmed: dict) -> Person:
    p.tag_behav_friction = confirmed.get(Tag.BEHAV_FRICTION, p.tag_behav_friction)
    p.tag_emotional_inv = confirmed.get(Tag.EMOTIONAL_INV, p.tag_emotional_inv)
    p.tag_high_awareness = confirmed.get(Tag.HIGH_AWARENESS, p.tag_high_awareness)
    p.tag_underserved = confirmed.get(Tag.UNDERSERVED, p.tag_underserved)
    p.tag_life_transition = confirmed.get(Tag.LIFE_TRANSITION, p.tag_life_transition)
    p.tag_relship_drift = confirmed.get(Tag.RELSHIP_DRIFT, p.tag_relship_drift)
    p.tag_high_earner = confirmed.get(Tag.HIGH_EARNER, p.tag_high_earner)
    return p


def infer_auto_tags(p: Person) -> dict:
    return {
        Tag.WEALTH_ACCUM: infer_wealth_accum(p),
        Tag.WEALTH_PRESERV: infer_wealth_preserv(p),
        Tag.FIN_COMPLEXITY: infer_fin_complexity(p),
        Tag.MISALIGNED_PROD: infer_misaligned_prod(p),
        Tag.BEHAV_FRICTION: p.tag_behav_friction,
        Tag.EMOTIONAL_INV: p.tag_emotional_inv,
        Tag.HIGH_AWARENESS: p.tag_high_awareness,
        Tag.UNDERSERVED: p.tag_underserved,
        Tag.LIFE_TRANSITION: p.tag_life_transition,
        Tag.RELSHIP_DRIFT: p.tag_relship_drift,
        Tag.HIGH_EARNER: p.tag_high_earner,
    }


def get_active_tags(p: Person) -> list:
    return [tag for tag, active in infer_auto_tags(p).items() if active]


def get_adviser_signals(p: Person) -> dict:
    suggestions = {s.tag: s for s in suggest_adviser_tags(p)}
    return {tag: (s.suggested, s.reason) for tag, s in suggestions.items()}
