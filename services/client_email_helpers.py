"""Parse and format client email lists (primary + secondary)."""
from __future__ import annotations

import re
from typing import Iterable, List, Optional

_EMAIL_SPLIT_RE = re.compile(r"[,\s;]+")
_EMAIL_VALID_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

# Firm-wide CC on recommendation emails (always included unless duplicate).
DEFAULT_RECOMMENDATION_CC = [
    "recommendations@equities4wealth.com",
    "sharveen@equities4wealth.com",
]


def parse_email_list(raw: Optional[str]) -> List[str]:
    """Split comma/semicolon/whitespace-separated emails; dedupe case-insensitively."""
    if not raw:
        return []
    out: List[str] = []
    seen = set()
    for part in _EMAIL_SPLIT_RE.split(str(raw).strip()):
        email = (part or "").strip()
        if not email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(email)
    return out


def format_email_list(emails: Iterable[str]) -> str:
    cleaned = parse_email_list(", ".join(emails))
    return ", ".join(cleaned)


def validate_email_list(emails: Iterable[str]) -> tuple[List[str], List[str]]:
    valid: List[str] = []
    invalid: List[str] = []
    for email in parse_email_list(", ".join(emails)):
        if _EMAIL_VALID_RE.match(email):
            valid.append(email)
        else:
            invalid.append(email)
    return valid, invalid


def get_client_secondary_emails(client) -> List[str]:
    raw = getattr(client, "secondary_emails", None) if client else None
    primary = (getattr(client, "email", None) or "").strip().lower()
    out: List[str] = []
    for email in parse_email_list(raw):
        if email.lower() == primary:
            continue
        out.append(email)
    return out


def build_recommendation_cc_list(client, extra_cc: Optional[str] = None) -> List[str]:
    """Firm CC + client secondary emails + optional UI override; deduped."""
    combined = list(DEFAULT_RECOMMENDATION_CC)
    combined.extend(get_client_secondary_emails(client))
    if extra_cc:
        combined.extend(parse_email_list(extra_cc))
    primary = (getattr(client, "email", None) or "").strip().lower()
    out: List[str] = []
    seen = set()
    for email in combined:
        key = email.lower()
        if not key or key == primary or key in seen:
            continue
        if not _EMAIL_VALID_RE.match(email):
            continue
        seen.add(key)
        out.append(email)
    return out
