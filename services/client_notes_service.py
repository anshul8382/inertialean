"""Timestamped append-only notes for client text fields."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.content_intelligence_person_source import (
    IC_QUESTIONNAIRE_MARKER,
    IC_TAGS_MARKER,
    _embed_marker,
    _parse_adviser_tags_from_synopsis,
    _parse_json_marker,
    strip_ic_markers,
)

NOTE_HEADER_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] ([^:\n]+):\s*$",
    re.MULTILINE,
)


def note_author_name(user) -> str:
    if not user:
        return "Unknown"
    return (
        getattr(user, "username", None)
        or getattr(user, "name", None)
        or getattr(user, "email", None)
        or f"User {getattr(user, 'id', '?')}"
    )


def format_note_entry(note_text: str, author: str, when: Optional[datetime] = None) -> str:
    text = (note_text or "").strip()
    if not text:
        return ""
    ts = (when or datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S")
    return f"\n[{ts}] {author}:\n{text}\n"


def parse_notes_entries(text: Optional[str]) -> List[Dict[str, Any]]:
    """Return note entries newest-first. Legacy blobs without headers become one entry."""
    human = strip_ic_markers(text).strip()
    if not human:
        return []

    matches = list(NOTE_HEADER_RE.finditer(human))
    if not matches:
        return [{"timestamp": None, "author": None, "body": human, "legacy": True}]

    entries: List[Dict[str, Any]] = []
    prefix = human[: matches[0].start()].strip()
    if prefix:
        entries.append({"timestamp": None, "author": None, "body": prefix, "legacy": True})

    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(human)
        body = human[start:end].strip()
        entries.append(
            {
                "timestamp": match.group(1),
                "author": match.group(2).strip(),
                "body": body,
                "legacy": False,
            }
        )

    entries.reverse()
    return entries


def append_planning_synopsis_note(
    existing: Optional[str],
    new_note_text: Optional[str],
    author: str,
    when: Optional[datetime] = None,
) -> Optional[str]:
    stored = _parse_adviser_tags_from_synopsis(existing)
    human = strip_ic_markers(existing).strip()
    entry = format_note_entry(new_note_text, author, when)
    if not entry:
        return existing
    human = (human + entry).strip() if human else entry.strip()
    if stored:
        return _embed_marker(human, IC_TAGS_MARKER, stored, None)
    return human or None


def append_background_notes_note(
    existing: Optional[str],
    new_note_text: Optional[str],
    author: str,
    when: Optional[datetime] = None,
) -> Optional[str]:
    stored = _parse_json_marker(existing, IC_QUESTIONNAIRE_MARKER)
    human = strip_ic_markers(existing).strip()
    entry = format_note_entry(new_note_text, author, when)
    if not entry:
        return existing
    human = (human + entry).strip() if human else entry.strip()
    if stored:
        return _embed_marker(human if human else None, IC_QUESTIONNAIRE_MARKER, stored, None)
    return human or None


def append_other_notes_note(
    existing: Optional[str],
    new_note_text: Optional[str],
    author: str,
    when: Optional[datetime] = None,
) -> Optional[str]:
    human = (existing or "").strip()
    entry = format_note_entry(new_note_text, author, when)
    if not entry:
        return existing
    human = (human + entry).strip() if human else entry.strip()
    return human or None
