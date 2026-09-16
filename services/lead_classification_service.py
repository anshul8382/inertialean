"""Classify CRM records: sales leads vs career applications.

Career applications arrive from the website careers form and are stored on the
same `lead` table (no schema change). They are excluded from the sales pipeline
and the daily leads report.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence

CAREER_SOURCE = "equities4wealth.com/careers"
CATEGORY_SALES = "sales"
CATEGORY_CAREER = "career"

_CAREER_NOTE_MARKERS = (
    "careers application",
    "career application",
)


def is_career_application_fields(
    *,
    source: Optional[str] = None,
    notes: Optional[str] = None,
    page: Optional[str] = None,
    form_type: Optional[str] = None,
) -> bool:
    """True when source/page/form_type/notes identify a careers submission."""
    for value in (source, page, form_type):
        if "career" in (value or "").strip().lower():
            return True
    notes_l = (notes or "").strip().lower()
    return any(marker in notes_l for marker in _CAREER_NOTE_MARKERS)


def is_career_application(lead: Any) -> bool:
    if lead is None:
        return False
    return is_career_application_fields(
        source=getattr(lead, "source", None),
        notes=getattr(lead, "notes", None),
    )


def exclude_career_applications(leads: Iterable[Any]) -> List[Any]:
    return [lead for lead in leads if not is_career_application(lead)]


def career_application_sql_filter():
    """SQLAlchemy clause matching career applications (lazy-imports models)."""
    from sqlalchemy import func, or_

    from models import Lead

    # COALESCE so NULL source/notes are not dropped by three-valued NOT (OR ...).
    source = func.coalesce(Lead.source, "")
    notes = func.coalesce(Lead.notes, "")
    return or_(
        source.ilike("%career%"),
        notes.ilike("%careers application%"),
        notes.ilike("%career application%"),
    )


def apply_category_filter(query, category: Optional[str]):
    """Restrict a Lead query. Default is sales (exclude career applications)."""
    from sqlalchemy import not_

    normalized = (category or CATEGORY_SALES).strip().lower()
    if normalized == CATEGORY_CAREER:
        return query.filter(career_application_sql_filter())
    if normalized == "all":
        return query
    return query.filter(not_(career_application_sql_filter()))


def canonical_source(
    *,
    source: Optional[str] = None,
    notes: Optional[str] = None,
    page: Optional[str] = None,
    form_type: Optional[str] = None,
    default: str = "equities4wealth.com",
) -> str:
    if is_career_application_fields(
        source=source, notes=notes, page=page, form_type=form_type
    ):
        return CAREER_SOURCE
    cleaned = (source or "").strip()
    return cleaned or default


def prepare_career_notes(
    message: str = "",
    *,
    role: str = "",
    location: str = "",
    available_to_join: str = "",
) -> str:
    """Build notes that stay detectable as a career application."""
    chunks: List[str] = []
    if role:
        chunks.append(f"Role: {role.strip()}")
    if location:
        chunks.append(f"Current location: {location.strip()}")
    if available_to_join:
        chunks.append(f"Available to join: {available_to_join.strip()}")
    extra = (message or "").strip()
    if extra:
        chunks.append(extra)
    body = " ".join(chunks) if chunks else ""
    body_l = body.lower()
    if any(body_l.startswith(marker) for marker in _CAREER_NOTE_MARKERS) or any(
        marker in body_l for marker in _CAREER_NOTE_MARKERS
    ):
        return body
    return f"Careers application {body}".strip()


def source_choices(current: Optional[str] = None) -> Sequence[tuple]:
    """(value, label) pairs for add/edit source selects."""
    choices = [
        ("referral", "Referral"),
        ("website", "Website"),
        ("equities4wealth.com", "Website (equities4wealth.com)"),
        (CAREER_SOURCE, "Career application"),
        ("social_media", "Social Media"),
        ("cold_call", "Cold Call"),
        ("other", "Other"),
    ]
    known = {value for value, _ in choices}
    if current and current not in known:
        choices.append((current, current))
    return choices
