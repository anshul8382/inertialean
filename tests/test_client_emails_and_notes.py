"""Tests for client secondary emails and timestamped notes."""
import pytest

from services.client_email_helpers import (
    build_recommendation_cc_list,
    format_email_list,
    parse_email_list,
    validate_email_list,
)
from services.client_notes_service import (
    append_background_notes_note,
    append_planning_synopsis_note,
    format_note_entry,
    parse_notes_entries,
)

pytestmark = pytest.mark.no_app


class _FakeClient:
    def __init__(self, email="primary@example.com", secondary_emails=None):
        self.email = email
        self.secondary_emails = secondary_emails


def test_parse_email_list_dedupes():
    assert parse_email_list("a@x.com, b@x.com; a@x.com") == ["a@x.com", "b@x.com"]


def test_validate_email_list():
    valid, invalid = validate_email_list(["good@example.com", "bad"])
    assert valid == ["good@example.com"]
    assert invalid == ["bad"]


def test_build_recommendation_cc_includes_secondary():
    client = _FakeClient(secondary_emails="spouse@example.com, assistant@example.com")
    cc = build_recommendation_cc_list(client)
    assert "spouse@example.com" in cc
    assert "assistant@example.com" in cc
    assert "recommendations@equities4wealth.com" in cc
    assert "primary@example.com" not in cc


def test_format_note_entry_and_parse():
    entry = format_note_entry("Met client today", "advisor1")
    assert "[20" in entry
    assert "advisor1:" in entry
    blob = entry.strip()
    parsed = parse_notes_entries(blob)
    assert len(parsed) == 1
    assert parsed[0]["body"] == "Met client today"
    assert parsed[0]["author"] == "advisor1"


def test_append_planning_synopsis_note_preserves_history():
    first = append_planning_synopsis_note(None, "First note", "user1")
    second = append_planning_synopsis_note(first, "Second note", "user2")
    entries = parse_notes_entries(second)
    assert len(entries) == 2
    bodies = {e["body"] for e in entries}
    assert "First note" in bodies
    assert "Second note" in bodies


def test_legacy_note_without_timestamp():
    parsed = parse_notes_entries("Old free-text note before timestamps")
    assert len(parsed) == 1
    assert parsed[0]["legacy"] is True
    assert parsed[0]["body"] == "Old free-text note before timestamps"


def test_format_email_list():
    assert format_email_list(["b@x.com", "a@x.com"]) == "b@x.com, a@x.com"
