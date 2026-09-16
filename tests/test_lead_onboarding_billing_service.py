"""Onboarding invoice gating rules (no DB)."""
from types import SimpleNamespace

import pytest

from services import lead_onboarding_billing_service as svc

pytestmark = pytest.mark.no_app


def test_preferred_agreement_requires_convertible(monkeypatch):
    monkeypatch.setattr(svc, "lead_convertible_agreements", lambda lead: [])
    assert svc.preferred_agreement_for_invoice(SimpleNamespace(id=1)) is None


def test_generate_invoice_blocks_without_signed_agreement(monkeypatch):
    monkeypatch.setattr(svc, "latest_invoice_for_lead", lambda lead_id: None)
    monkeypatch.setattr(svc, "preferred_agreement_for_invoice", lambda lead: None)
    result = svc.generate_onboarding_invoice(SimpleNamespace(id=1), user_id=9)
    assert "error" in result
    assert "sign" in result["error"].lower()
