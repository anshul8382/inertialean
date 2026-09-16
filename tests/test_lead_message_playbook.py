"""WhatsApp playbook load / select / fill."""
from pathlib import Path

import pytest

from services.lead_message_playbook_service import (
    fill_template,
    list_stages,
    load_playbook_stages,
    select_stage_for_lead,
)

pytestmark = pytest.mark.no_app

PLAYBOOK = Path(__file__).resolve().parents[1] / "docs" / "onboarding_whatsapp_playbook.md"


def test_playbook_loads_expected_stages():
    stages = load_playbook_stages(PLAYBOOK)
    assert "first_contact" in stages
    assert "followup_1" in stages
    assert "followup_2" in stages
    assert "risk_invite" in stages
    assert "annual_risk_refresh" in stages
    assert "{first_name}" in stages["first_contact"]["body"]


def test_list_stages_ordered():
    rows = list_stages(PLAYBOOK)
    keys = [r["key"] for r in rows]
    assert keys.index("first_contact") < keys.index("followup_2")


def test_fill_template_strips_empty_context():
    text = fill_template(
        "Hi {first_name},\n\n{note_snippet}\n\nBye {sender}",
        {"first_name": "Pat", "note_snippet": "", "sender": "Inertia"},
    )
    assert "Pat" in text
    assert "Context" not in text


def test_select_stage_for_new_lead():
    lead = type("L", (), {"status": "new"})()
    assert select_stage_for_lead(lead) == "first_contact"


def test_select_stage_risk_invite():
    lead = type("L", (), {"status": "contacted"})()
    assert select_stage_for_lead(lead, has_risk=False) == "risk_invite"


def test_select_stage_proposal_nudge():
    lead = type("L", (), {"status": "proposal_sent"})()
    assert select_stage_for_lead(lead, has_risk=True) == "proposal_sent_nudge"
