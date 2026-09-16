"""Onboarding hub workflow pointer (no DB)."""
import pytest

from services.lead_onboarding_hub_service import _enrich_steps_with_workflow

pytestmark = pytest.mark.no_app


def test_order_current_is_first_incomplete():
    steps = [
        {"key": "contact", "label": "Contact", "done": True, "detail": "ok"},
        {"key": "meeting", "label": "Meeting", "done": True, "detail": "ok"},
        {"key": "risk", "label": "Risk", "done": False, "detail": "todo"},
        {"key": "proposal", "label": "Proposal", "done": False, "detail": "todo"},
        {"key": "kyc", "label": "KYC", "done": False, "detail": "todo"},
        {"key": "agreement", "label": "Agreement", "done": False, "detail": "todo"},
    ]
    out = {
        "fully_converted": False,
        "quiz_url": "/q",
        "proposal_url": "/p",
        "create_agreement_url": "/a",
        "agreements_url": "/as",
        "mark_kyc_url": "/k",
        "generate_invoice_url": "/i",
        "mark_risk_sent_url": "/rs",
        "mark_contacted_url": "/mc",
        "new_meeting_url": "/nm",
        "has_signed_agreement": False,
        "has_kyc": False,
        "has_paid": False,
        "zoho_configured": False,
        "convert_url": None,
    }
    enriched = _enrich_steps_with_workflow(steps, out)
    assert [s["key"] for s in enriched["steps"]] == [
        "contact",
        "meeting",
        "risk",
        "proposal",
        "kyc",
        "agreement",
    ]
    assert enriched["current_step"]["key"] == "risk"
    assert enriched["next_action"]["step_key"] == "risk"
    assert enriched["steps_by_key"]["kyc"]["index"] == 5
    assert enriched["steps_by_key"]["agreement"]["index"] == 6
