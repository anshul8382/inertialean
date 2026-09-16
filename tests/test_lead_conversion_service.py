"""Tests for lead → client conversion."""
import pytest

pytestmark = pytest.mark.no_app


def test_convert_links_existing_client_by_email(monkeypatch):
    from services.lead_conversion_service import convert_lead_to_client

    existing = type("C", (), {"id": 42, "name": "Smitha Nair", "email": "sm08nair@gmail.com"})()
    lead = type("L", (), {"id": 7, "client_id": None, "user_id": 3, "status": "open"})()
    finalized = {}

    monkeypatch.setattr(
        "services.lead_conversion_service.find_client_by_email",
        lambda email: existing if email == "sm08nair@gmail.com" else None,
    )
    monkeypatch.setattr(
        "services.lead_conversion_service.require_convertible_agreement",
        lambda lead_obj: None,
    )

    def _finalize(lead_obj, client_obj, *, referral_by):
        finalized["client"] = client_obj

    monkeypatch.setattr("services.lead_conversion_service._finalize_lead_conversion", _finalize)

    client, created_new = convert_lead_to_client(
        lead,
        name="V S Menon (smitha nair Dad)",
        email="sm08nair@gmail.com",
        phone="+91 97654 07068",
        risk_profile="Conservative",
        referral_by="BNI",
        acting_user_id=1,
    )

    assert created_new is False
    assert client is existing


def test_convert_not_blocked_without_signed_agreement(monkeypatch):
    """Early convert: KYC / agreement / paid invoice do not block client activation."""
    from services import lead_conversion_service as svc

    lead = type("L", (), {"id": 7, "client_id": None, "user_id": 3, "status": "open"})()
    monkeypatch.setattr(svc, "lead_has_convertible_agreement", lambda lead_obj: False)
    monkeypatch.setattr(
        "services.lead_kyc_service.lead_has_kyc_documents",
        lambda lead_id: False,
    )
    monkeypatch.setattr(
        "services.lead_onboarding_billing_service.lead_has_paid_invoice",
        lambda lead_obj: False,
    )
    assert svc.convert_block_reason(lead) is None


def test_agreement_pdf_required_for_convertible_status():
    from services.lead_conversion_service import _agreement_has_pdf

    ag_ok = type("A", (), {"generated_pdf_path": "/static/agreements/a.pdf"})()
    ag_empty = type("A", (), {"generated_pdf_path": ""})()
    ag_none = type("A", (), {"generated_pdf_path": None})()

    assert _agreement_has_pdf(ag_ok) is True
    assert _agreement_has_pdf(ag_empty) is False
    assert _agreement_has_pdf(ag_none) is False


def test_convert_block_reason_only_career():
    from services.lead_conversion_service import convert_block_reason

    lead = type("L", (), {"id": 1, "client_id": None, "status": "open"})()
    assert convert_block_reason(lead) is None


def test_onboarding_checklist_incomplete(monkeypatch):
    from services import lead_conversion_service as svc

    lead = type("L", (), {"id": 1, "client_id": 9})()
    monkeypatch.setattr(svc, "lead_has_convertible_agreement", lambda lead_obj: False)
    monkeypatch.setattr(
        "services.lead_kyc_service.lead_has_kyc_documents",
        lambda lead_id: False,
    )
    monkeypatch.setattr(
        "services.lead_onboarding_billing_service.lead_has_paid_invoice",
        lambda lead_obj: False,
    )
    missing = svc.onboarding_checklist_incomplete(lead)
    assert "signed agreement" in missing
    assert "KYC" in missing
    assert "paid invoice" in missing


def test_convert_blocked_for_career_application(monkeypatch):
    from services import lead_conversion_service as svc

    lead = type(
        "L",
        (),
        {
            "id": 1,
            "client_id": None,
            "source": "careers",
            "notes": "career application",
        },
    )()

    monkeypatch.setattr(
        "services.lead_classification_service.is_career_application",
        lambda _lead: True,
    )
    # convert_block_reason imports inside try — ensure the imported name is patched
    import services.lead_classification_service as class_svc

    monkeypatch.setattr(class_svc, "is_career_application", lambda _lead: True)

    reason = svc.convert_block_reason(lead)
    assert reason is not None
    assert "career" in reason.lower()
