"""
Agreement / LOE date resolution and display formatting for template substitution.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Dict, Optional, Tuple

from models import Agreement

# Placeholder looked like a pasted date, not a variable name (e.g. <<26th Aug 2025>>).
_LITERAL_DATE_PLACEHOLDER = re.compile(
    r"^\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}$",
    re.IGNORECASE,
)

STANDARD_DATE_PLACEHOLDERS = frozenset(
    {
        "date",
        "agreement_date",
        "loe_date",
        "letter_date",
        "engagement_date",
        "signature_date",
        "agreement_date_iso",
        "date_iso",
    }
)


def _ordinal_day(day: int) -> str:
    if 11 <= (day % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def format_agreement_date_display(d: date) -> str:
    """e.g. 26th Aug 2025 (matches common LOE template style)."""
    return f"{_ordinal_day(d.day)} {d.strftime('%b %Y')}"


def format_agreement_date_long(d: date) -> str:
    """e.g. 26 August 2025."""
    return f"{d.day} {d.strftime('%B %Y')}"


def format_agreement_date_iso(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def is_literal_date_placeholder(name: str) -> bool:
    return bool(_LITERAL_DATE_PLACEHOLDER.match((name or "").strip()))


def resolve_agreement_date(agreement: Agreement) -> date:
    """
    Date printed on the LOE / agreement.

    Priority: AgreementVariable agreement_date → signed_date → sent_date → today.
    """
    for v in agreement.variables or []:
        if (v.variable_name or "").strip() != "agreement_date":
            continue
        raw = (v.variable_value or "").strip()
        if not raw:
            continue
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            pass

    for attr in ("signed_date", "sent_date"):
        raw = getattr(agreement, attr, None)
        if raw is None:
            continue
        if hasattr(raw, "date"):
            return raw.date()
        if isinstance(raw, date):
            return raw

    return date.today()


def build_date_substitution_values(agreement: Agreement) -> Dict[str, str]:
    """All standard date placeholders for one agreement."""
    d = resolve_agreement_date(agreement)
    display = format_agreement_date_display(d)
    iso = format_agreement_date_iso(d)
    long_fmt = format_agreement_date_long(d)
    return {
        "date": display,
        "agreement_date": display,
        "loe_date": display,
        "letter_date": display,
        "engagement_date": display,
        "signature_date": display,
        "agreement_date_long": long_fmt,
        "agreement_date_iso": iso,
        "date_iso": iso,
    }


def apply_date_substitutions(
    agreement: Agreement,
    template_keys: set,
    existing_data: Dict[str, str],
    *,
    overwrite_empty_only: bool = True,
) -> None:
    """Fill date placeholders in existing_data (mutates dict)."""
    values = build_date_substitution_values(agreement)

    def _should_set(key: str) -> bool:
        if key not in template_keys:
            return False
        if not overwrite_empty_only:
            return True
        return not (existing_data.get(key) or "").strip()

    for key, val in values.items():
        if _should_set(key):
            existing_data[key] = val

    for key in template_keys:
        if not is_literal_date_placeholder(key):
            continue
        if not _should_set(key):
            continue
        existing_data[key] = values["agreement_date"]
