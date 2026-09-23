"""Unit tests for regulatory identity capture (PAN / agreement start)."""
from types import SimpleNamespace

import pytest

from services import regulatory_identity_capture_service as svc

pytestmark = pytest.mark.no_app


def test_normalize_and_validate_pan():
    assert svc.normalize_pan(" abcde1234f ") == "ABCDE1234F"
    assert svc.is_valid_pan("ABCDE1234F") is True
    assert svc.is_valid_pan("BAD") is False
    assert svc.is_valid_pan("") is False


def test_regulatory_gaps_requires_pan(monkeypatch):
    lead = SimpleNamespace(id=1)
    monkeypatch.setattr(svc, "lead_has_regulatory_pan", lambda lead_id: False)
    monkeypatch.setattr(
        "services.lead_conversion_service.lead_convertible_agreements",
        lambda lead: [SimpleNamespace(id=9, signed_date="2026-03-01")],
    )
    gaps = svc.regulatory_gaps_for_lead(lead)
    assert "PAN on KYC profile" in gaps
