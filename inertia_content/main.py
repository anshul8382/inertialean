"""
CLI demo for Inertia Content Intelligence module.
Run: python main.py

Shows:
  1. Tag inference for a sample person
  2. Content matching — ranked person list for a content piece
  3. Reverse lookup — content list for a person
  4. AI tag suggestion (requires ANTHROPIC_API_KEY)
"""

from datetime import date
from inertia_content import (
    Person, ContentPiece, SendLog,
    Tag, Category, AgeBracket, AssetBracket, SurplusBracket,
    LifeEvent, Milestone, ContentFormat, Decision, Engagement,
    infer_auto_tags, get_active_tags, get_adviser_signals,
    rank_persons_for_content, find_content_for_person, format_match_results,
)


# ── Sample data ───────────────────────────────────────────────────────────────

def make_sample_persons():
    return [
        Person(
            id=1, name="Rahul Mehta",
            category=Category.CLIENT,
            profession="Technology",
            age_bracket=AgeBracket.A35_44,
            city="Pune", is_nri=False,
            holds_mf=True, holds_stock=True,
            investable_assets=AssetBracket.L50_1CR,
            monthly_surplus=SurplusBracket.L1_2,
            has_ca_rm_mfd=False,
            recent_life_event=LifeEvent.NONE,
            upcoming_milestone=Milestone.CHILD_EDU,
            tag_high_awareness=True,
            tag_high_earner=True,
            last_contacted=date(2025, 4, 15),
            last_engaged=True,
            engagement_score=4,
            max_msgs_per_month=2,
        ),
        Person(
            id=2, name="Priya Sharma",
            category=Category.LEAD,
            profession="Medical / Healthcare",
            age_bracket=AgeBracket.A45_54,
            city="Mumbai", is_nri=False,
            holds_mf=True, holds_lic=True, holds_fd=True,
            investable_assets=AssetBracket.L1_2CR,
            monthly_surplus=SurplusBracket.GT3L,
            has_ca_rm_mfd=True,
            recent_life_event=LifeEvent.NONE,
            upcoming_milestone=Milestone.RETIREMENT_5,
            tag_behav_friction=True,
            tag_underserved=True,
            tag_high_earner=True,
            last_contacted=date(2025, 1, 10),
            engagement_score=2,
            max_msgs_per_month=1,
        ),
        Person(
            id=3, name="Arjun Nair",
            category=Category.CLIENT,
            profession="Business Owner",
            age_bracket=AgeBracket.A35_44,
            city="Bangalore",
            holds_mf=True, holds_stock=True, holds_pms_aif=True, holds_real_estate=True,
            investable_assets=AssetBracket.GT2CR,
            monthly_surplus=SurplusBracket.GT3L,
            is_business_owner=True,
            recent_life_event=LifeEvent.BUSINESS_EXIT,
            upcoming_milestone=Milestone.BIZ_TRANSITION,
            tag_high_awareness=True,
            tag_life_transition=True,
            tag_high_earner=True,
            last_contacted=date(2025, 4, 20),
            last_engaged=True,
            engagement_score=5,
            max_msgs_per_month=3,
        ),
        Person(
            id=4, name="Sunita Kapoor",
            category=Category.INACTIVE_CLIENT,
            profession="Government / PSU",
            age_bracket=AgeBracket.A45_54,
            city="Pune",
            holds_mf=True, holds_lic=True, holds_ulip=True, holds_fd=True,
            investable_assets=AssetBracket.L25_50,
            monthly_surplus=SurplusBracket.K50_1L,
            has_ca_rm_mfd=True,
            tag_emotional_inv=True,
            tag_underserved=True,
            tag_relship_drift=True,
            last_contacted=date(2024, 11, 15),
            engagement_score=1,
            max_msgs_per_month=1,
        ),
        Person(
            id=5, name="Vikram Joshi",
            category=Category.PROSPECT,
            profession="Finance / Banking",
            age_bracket=AgeBracket.A35_44,
            city="NRI – US", is_nri=True, is_cross_border=True,
            holds_mf=True, holds_stock=True,
            investable_assets=AssetBracket.GT2CR,
            monthly_surplus=SurplusBracket.GT3L,
            recent_life_event=LifeEvent.NRI_RETURN,
            upcoming_milestone=Milestone.NRI_MOVE,
            tag_high_awareness=True,
            tag_life_transition=True,
            tag_high_earner=True,
            engagement_score=0,
            max_msgs_per_month=2,
        ),
    ]


def make_sample_content():
    return [
        ContentPiece(
            id=1, title="What stops investors — the invisible friction loop",
            format=ContentFormat.LINKEDIN, topic_category="Investor Psychology",
            tag_behav_friction=True, tag_emotional_inv=True, tag_relship_drift=True,
        ),
        ContentPiece(
            id=2, title="Comfort of doing nothing — the hidden cost of pause",
            format=ContentFormat.EMAIL, topic_category="Investor Psychology",
            tag_wealth_accum=True, tag_wealth_preserv=True,
            tag_behav_friction=True, tag_relship_drift=True,
        ),
        ContentPiece(
            id=3, title="Market cycles as emotional cycles",
            format=ContentFormat.YOUTUBE, topic_category="Investor Psychology",
            tag_wealth_accum=True, tag_emotional_inv=True, tag_high_awareness=True,
        ),
        ContentPiece(
            id=4, title="Affluent but anxious — naming the feeling",
            format=ContentFormat.EMAIL, topic_category="Hook / Awareness",
            tag_wealth_accum=True, tag_misaligned_prod=True,
            tag_behav_friction=True, tag_high_earner=True,
        ),
        ContentPiece(
            id=5, title="What good financial advice actually looks like",
            format=ContentFormat.LINKEDIN, topic_category="Trust Building",
            tag_misaligned_prod=True, tag_high_awareness=True, tag_underserved=True,
        ),
        ContentPiece(
            id=6, title="Complexity moments — when DIY breaks down",
            format=ContentFormat.LINKEDIN, topic_category="Education",
            tag_fin_complexity=True, tag_high_awareness=True,
            tag_life_transition=True, tag_high_earner=True,
        ),
        ContentPiece(
            id=7, title="Staying the course — patience vs neglect",
            format=ContentFormat.WHATSAPP, topic_category="Investor Psychology",
            tag_wealth_accum=True, tag_wealth_preserv=True,
            tag_behav_friction=True, tag_relship_drift=True,
        ),
    ]


# ── Demo ─────────────────────────────────────────────────────────────────────

def demo_tag_inference():
    print("\n" + "="*70)
    print("1. TAG INFERENCE — Vikram Joshi (NRI prospect)")
    print("="*70)
    persons = make_sample_persons()
    vikram = persons[4]

    auto_tags = infer_auto_tags(vikram)
    active = [Tag.LABELS[t] for t, v in auto_tags.items() if v]

    print(f"\nPerson: {vikram.name} | {vikram.category} | {vikram.age_bracket} | {vikram.city}")
    print(f"Active tags ({len(active)}): {', '.join(active)}")

    print("\nAdviser signals (partial inferences for behavioural tags):")
    signals = get_adviser_signals(vikram)
    for tag_id, (fired, reason) in signals.items():
        status = "⚡ SIGNAL" if fired else "  quiet"
        print(f"  {status}  {Tag.LABELS[tag_id]}: {reason}")


def demo_content_matching():
    print("\n" + "="*70)
    print("2. CONTENT MATCHING — 'Complexity moments' → who should get it?")
    print("="*70)
    persons = make_sample_persons()
    content_list = make_sample_content()
    complexity_piece = content_list[5]  # "Complexity moments"

    print(f"\nContent: {complexity_piece.title}")
    print(f"Tags: {', '.join(Tag.LABELS[t] for t in complexity_piece.active_tags())}")

    results = rank_persons_for_content(persons, complexity_piece, min_match=1)
    print(f"\nMatched {len(results)} of {len(persons)} persons:\n")
    print(format_match_results(results))


def demo_reverse_lookup():
    print("\n" + "="*70)
    print("3. REVERSE LOOKUP — Priya Sharma → which content fits her?")
    print("="*70)
    persons = make_sample_persons()
    content_list = make_sample_content()
    priya = persons[1]

    active = [Tag.LABELS[t] for t in get_active_tags(priya)]
    print(f"\nPerson: {priya.name} | Tags: {', '.join(active)}")

    matches = find_content_for_person(priya, content_list, min_match=1)
    print(f"\n{len(matches)} content pieces match:\n")
    for m in matches:
        print(f"  [{m['match_count']} tags]  {m['content'].title}")
        print(f"           Matching: {', '.join(m['matched_tags'])}")


def demo_ai_tagger():
    print("\n" + "="*70)
    print("4. AI TAG SUGGESTION (requires ANTHROPIC_API_KEY)")
    print("="*70)
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("  Skipped — set ANTHROPIC_API_KEY environment variable to enable.")
        return

    from inertia_content import suggest_tags
    sample_content = """
    Most investors know they should review their portfolio regularly.
    They know they should have a financial plan. They know FDs alone
    won't beat inflation over 20 years. And yet — nothing happens.

    The problem isn't knowledge. It's the friction between knowing and doing.
    The feeling that things are fine enough for now. That clarity will come
    after the next market correction, the next bonus, the next quarter.

    It never does. The question isn't whether to act. It's whether
    you can afford to keep waiting.
    """

    print("\nContent snippet: 'Most investors know they should...'")
    suggestion = suggest_tags(sample_content)
    print(f"\nSuggested tags:")
    for tag_id, active in suggestion.tags.items():
        if active:
            print(f"  ✓ {Tag.LABELS.get(tag_id, tag_id)}: {suggestion.reasons.get(tag_id, '')}")


if __name__ == "__main__":
    demo_tag_inference()
    demo_content_matching()
    demo_reverse_lookup()
    demo_ai_tagger()
    print("\n" + "="*70)
    print("Done. Connect to MySQL via Database() to persist data.")
    print("="*70 + "\n")
