"""KYC readiness should require documents or non-manual verification."""
from types import SimpleNamespace

import pytest

from services import lead_kyc_service as svc

pytestmark = pytest.mark.no_app


def test_manual_submitted_profile_is_not_treated_as_complete(monkeypatch):
    monkeypatch.setattr(svc, "list_kyc_documents", lambda lead_id: [])
    monkeypatch.setattr("services.lead_kyc_profile_service.kyc_table_ready", lambda: True)
    monkeypatch.setattr(
        "services.lead_kyc_profile_service.get_or_create_profile",
        lambda lead_id: SimpleNamespace(status="submitted", provider="manual"),
    )
    assert svc.lead_has_kyc_documents(123) is False


def test_non_manual_verified_profile_counts_as_complete(monkeypatch):
    monkeypatch.setattr(svc, "list_kyc_documents", lambda lead_id: [])
    monkeypatch.setattr("services.lead_kyc_profile_service.kyc_table_ready", lambda: True)
    monkeypatch.setattr(
        "services.lead_kyc_profile_service.get_or_create_profile",
        lambda lead_id: SimpleNamespace(status="verified", provider="ckyc_api"),
    )
    assert svc.lead_has_kyc_documents(123) is True
