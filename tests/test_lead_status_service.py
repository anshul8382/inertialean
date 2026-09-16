"""Lead status note merge (no DB)."""
from datetime import datetime

import pytest

from services.lead_status_service import (
    apply_status_fields,
    build_status_note_line,
    merge_status_change_notes,
)
from services.content_intelligence_person_source import IC_QUESTIONNAIRE_MARKER, strip_ic_markers

pytestmark = pytest.mark.no_app


def test_status_note_line_includes_notes():
    when = datetime(2026, 9, 9)
    assert (
        build_status_note_line("proposal_sent", "Called back", when=when)
        == "[2026-09-09] Status → proposal_sent: Called back"
    )


def test_status_note_line_without_notes():
    when = datetime(2026, 9, 9)
    assert build_status_note_line("contacted", "  ", when=when) == "[2026-09-09] Status → contacted"


def test_merge_appends_and_keeps_ic_marker():
    existing = "First contact\n\n" + IC_QUESTIONNAIRE_MARKER + '{"city":"Mumbai"}'
    merged = merge_status_change_notes(
        existing, "qualified", "Good fit", when=datetime(2026, 9, 9)
    )
    assert IC_QUESTIONNAIRE_MARKER in (merged or "")
    human = strip_ic_markers(merged)
    assert "First contact" in human
    assert "[2026-09-09] Status → qualified: Good fit" in human


def test_apply_status_fields_drops_and_notes():
    lead = type("L", (), {"status": "new", "is_active": True, "notes": None, "updated_at": None})()
    apply_status_fields(lead, "dropped", "Not interested")
    assert lead.status == "dropped"
    assert lead.is_active is False
    assert "Status → dropped: Not interested" in (lead.notes or "")
    assert lead.updated_at is not None
