from datetime import date
from typing import List, Optional
from .models import Person, ContentPiece, MatchResult, Tag
from .tags import infer_auto_tags, get_active_tags


def days_since_contact(p: Person) -> Optional[int]:
    if not p.last_contacted:
        return None
    return (date.today() - p.last_contacted).days


def is_ok_to_contact(p: Person) -> tuple[bool, str]:
    """
    Returns (ok: bool, reason: str).
    Checks frequency limit against days since last contact.
    """
    days = days_since_contact(p)
    if days is None:
        return True, "Never contacted — clear to send"

    # Derive minimum gap from max_msgs_per_month
    # 1 msg/mo = 30 days gap, 2 = 15 days, 3 = 10 days, 4 = 7 days
    min_gap = max(7, 30 // max(p.max_msgs_per_month, 1))

    if days < min_gap:
        return False, f"Contacted {days}d ago, min gap is {min_gap}d ({p.max_msgs_per_month} msg/mo)"
    return True, f"Last contacted {days}d ago — ok to send"


def match_person_to_content(person: Person, content: ContentPiece) -> MatchResult:
    """
    Scores a single person against a content piece.
    Returns a MatchResult with count, matched tag names, and contact eligibility.
    """
    person_tags = set(get_active_tags(person))
    content_tags = set(content.active_tags())
    matched = list(person_tags & content_tags)
    ok, reason = is_ok_to_contact(person)

    return MatchResult(
        person=person,
        match_count=len(matched),
        matched_tags=matched,
        days_since_contact=days_since_contact(person),
        ok_to_contact=ok,
        contact_reason=reason,
    )


def rank_persons_for_content(
    persons: List[Person],
    content: ContentPiece,
    min_match: int = 1,
    only_contactable: bool = False,
) -> List[MatchResult]:
    """
    Given a list of persons and a content piece, returns ranked MatchResults.

    Ranking logic:
    1. Tag match count (descending) — primary sort
    2. Engagement score (descending) — tiebreaker
    3. Days since contact (descending) — longest gap first among ties

    Args:
        persons:          Full list of Person objects
        content:          ContentPiece to match against
        min_match:        Minimum tag matches to include in results (default 1)
        only_contactable: If True, filter out persons flagged as too-soon

    Returns:
        Sorted list of MatchResult
    """
    results = []
    for p in persons:
        r = match_person_to_content(p, content)
        if r.match_count < min_match:
            continue
        if only_contactable and not r.ok_to_contact:
            continue
        results.append(r)

    results.sort(
        key=lambda r: (
            r.match_count,
            r.person.engagement_score,
            r.days_since_contact if r.days_since_contact is not None else 0,
        ),
        reverse=True,
    )
    return results


def find_content_for_person(
    person: Person,
    content_list: List[ContentPiece],
    min_match: int = 1,
) -> list:
    """
    Reverse lookup — given a person, find all content pieces that match them.
    Returns list of (ContentPiece, match_count, matched_tags) sorted by match count.
    """
    results = []
    person_tags = set(get_active_tags(person))

    for content in content_list:
        content_tags = set(content.active_tags())
        matched = list(person_tags & content_tags)
        if len(matched) >= min_match:
            results.append({
                "content": content,
                "match_count": len(matched),
                "matched_tags": [Tag.LABELS.get(t, t) for t in matched],
            })

    results.sort(key=lambda x: x["match_count"], reverse=True)
    return results


def format_match_results(results: List[MatchResult]) -> str:
    """Pretty-print match results for CLI use."""
    if not results:
        return "No matches found."

    lines = []
    lines.append(f"{'#':<3} {'Name':<20} {'Category':<16} {'Matches':<8} {'Tags':<45} {'Days':<6} {'Contact?'}")
    lines.append("-" * 120)

    for i, r in enumerate(results, 1):
        tag_labels = ", ".join(Tag.LABELS.get(t, t) for t in r.matched_tags)
        days = str(r.days_since_contact) if r.days_since_contact is not None else "—"
        contact = "✓" if r.ok_to_contact else "✗ " + r.contact_reason[:30]
        lines.append(
            f"{i:<3} {r.person.name:<20} {r.person.category:<16} "
            f"{r.match_count:<8} {tag_labels:<45} {days:<6} {contact}"
        )

    return "\n".join(lines)
