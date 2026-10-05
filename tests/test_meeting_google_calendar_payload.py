"""Meeting → Google Calendar: only title, times, attendees (no notes/description)."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.no_app


def test_meeting_event_payload_omits_notes_and_description():
    from services.google_calendar_service import _meeting_event_payload

    organizer = SimpleNamespace(id=1, email="host@example.com")
    client = SimpleNamespace(email="client@example.com")
    participant = SimpleNamespace(
        user=SimpleNamespace(email="advisor@example.com")
    )
    meeting = SimpleNamespace(
        id=99,
        title="Portfolio review",
        description="Internal description must not sync",
        notes="Secret notes must not sync\nGoogle Meet: https://meet.google.com/old",
        meeting_date=datetime(2026, 10, 5, 10, 0, 0),
        duration=45,
        client=client,
        lead=None,
        participants_assoc=[participant],
    )

    body = _meeting_event_payload(meeting, organizer, request_meet=True)

    assert body["summary"] == "Meeting · Portfolio review"
    assert body.get("description", "") == ""
    assert "Notes" not in (body.get("description") or "")
    assert "Secret" not in (body.get("description") or "")
    assert "start" in body and "end" in body
    emails = {a["email"] for a in body["attendees"]}
    assert "client@example.com" in emails
    assert "advisor@example.com" in emails
    assert "host@example.com" not in emails
    assert body["conferenceData"]["createRequest"]["conferenceSolutionKey"]["type"] == (
        "hangoutsMeet"
    )


def test_quick_add_meeting_url_has_no_details():
    from services.google_calendar_service import quick_add_google_calendar_url_for_meeting

    meeting = SimpleNamespace(
        title="Kickoff",
        meeting_date=datetime(2026, 10, 5, 10, 0, 0),
        duration=30,
        description="desc",
        notes="notes",
    )
    url = quick_add_google_calendar_url_for_meeting(meeting, "https://app.example/meetings/1")
    assert "action=TEMPLATE" in url
    assert "text=" in url
    assert "dates=" in url
    assert "details=" not in url
    assert "notes" not in url.lower()


def test_merge_meet_link_updates_app_notes_only():
    from services.google_calendar_service import _merge_google_meet_into_notes

    out = _merge_google_meet_into_notes(
        "Client prefers morning\nGoogle Meet: https://meet.google.com/old-xxx",
        "https://meet.google.com/new-yyy",
    )
    assert "Client prefers morning" in out
    assert "https://meet.google.com/new-yyy" in out
    assert "old-xxx" not in out
