"""Unit tests for Leegality helpers (no live API)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.no_app


def test_normalize_phone_strips_country_code():
    from services.leegality_service import normalize_phone

    assert normalize_phone("+91 98765 43210") == "9876543210"
    assert normalize_phone("09876543210") == "9876543210"
    assert normalize_phone("bad") is None


def test_verify_webhook_mac(monkeypatch):
    from services import leegality_service as svc

    monkeypatch.setenv("LEEGALITY_PRIVATE_SALT", "test-salt")
    # Bypass flask config
    monkeypatch.setattr(svc, "_cfg", lambda key, default="": "test-salt" if key == "LEEGALITY_PRIVATE_SALT" else default)

    import hashlib
    import hmac

    doc = "01KMFEE9T8WPY0WPWXQHQ7DTJ2"
    mac = hmac.new(b"test-salt", doc.encode(), hashlib.sha1).hexdigest()
    assert svc.verify_webhook_mac(doc, mac) is True
    assert svc.verify_webhook_mac(doc, "deadbeef") is False


def test_invitee_from_lead_requires_contact():
    from services.leegality_service import LeegalityError, invitee_from_lead

    with pytest.raises(LeegalityError):
        invitee_from_lead(SimpleNamespace(name="A", email="", phone="", client=None))

    with pytest.raises(LeegalityError, match="mobile"):
        invitee_from_lead(
            SimpleNamespace(name="Ada", email="ada@example.com", phone="", client=None)
        )

    inv = invitee_from_lead(
        SimpleNamespace(name="Ada", email="ada@example.com", phone="+919876543210", client=None)
    )
    assert inv["name"] == "Ada"
    assert inv["email"] == "ada@example.com"
    assert inv["phone"] == "9876543210"


def test_invitee_name_from_agreement_client_name():
    from services.leegality_service import invitee_from_agreement

    lead = SimpleNamespace(
        name="Lead Name",
        email="crm@example.com",
        phone="9999999999",
        client=None,
    )
    agreement = SimpleNamespace(
        lead=lead,
        variables=[
            SimpleNamespace(variable_name="client_name", variable_value="PAN Client Name"),
            SimpleNamespace(variable_name="client_email", variable_value="aadhaar@example.com"),
            SimpleNamespace(variable_name="client_mobile", variable_value="9876543210"),
        ],
        agreement_data="{}",
    )
    inv = invitee_from_agreement(agreement, lead)
    assert inv["name"] == "PAN Client Name"
    assert inv["email"] == "aadhaar@example.com"
    assert inv["phone"] == "9876543210"


def test_signing_contact_prefills_from_lead():
    from services.leegality_service import signing_contact_for_form

    lead = SimpleNamespace(
        name="Lead",
        email="lead@example.com",
        phone="9876543210",
        client=None,
    )
    agreement = SimpleNamespace(
        lead=lead,
        variables=[SimpleNamespace(variable_name="client", variable_value="Agreement Client")],
        agreement_data="{}",
    )
    sc = signing_contact_for_form(agreement, lead)
    assert sc["name"] == "Agreement Client"
    assert sc["email"] == "lead@example.com"
    assert sc["mobile"] == "9876543210"
    assert sc["email_from_lead"] is True
    assert sc["mobile_from_lead"] is True

    agreement.variables = [
        SimpleNamespace(variable_name="client", variable_value="Agreement Client"),
        SimpleNamespace(variable_name="client_email", variable_value="aadhaar@example.com"),
        SimpleNamespace(variable_name="client_mobile", variable_value="9123456780"),
    ]
    sc2 = signing_contact_for_form(agreement, lead)
    assert sc2["email"] == "aadhaar@example.com"
    assert sc2["mobile"] == "9123456780"
    assert sc2["email_from_lead"] is False


def test_invitee_ignores_crm_contact_without_agreement_fields():
    from services.leegality_service import LeegalityError, invitee_from_agreement

    lead = SimpleNamespace(
        name="Lead Name",
        email="crm@example.com",
        phone="9999999999",
        client=None,
    )
    agreement = SimpleNamespace(
        lead=lead,
        variables=[SimpleNamespace(variable_name="client", variable_value="Agreement Client")],
        agreement_data="{}",
    )
    with pytest.raises(LeegalityError, match="Agreement is missing"):
        invitee_from_agreement(agreement, lead)


def test_apply_webhook_rejects_bad_mac(monkeypatch):
    from services import leegality_service as svc

    monkeypatch.setattr(svc, "verify_webhook_mac", lambda *a, **k: False)
    result = svc.apply_webhook_payload({"documentId": "x", "mac": "bad"})
    assert result == {"ok": False, "error": "invalid_mac"}


def test_apply_webhook_updates_agreement(monkeypatch):
    from services import leegality_service as svc

    agreement = SimpleNamespace(id=42, agreement_data="{}", updated_at=None, status="sent", signed_date=None)
    monkeypatch.setattr(svc, "verify_webhook_mac", lambda *a, **k: True)
    monkeypatch.setattr(svc, "_find_agreement_by_document_id", lambda _d: agreement)
    monkeypatch.setattr(svc, "save_leegality_meta", lambda *a, **k: {"document_id": "doc1"})
    monkeypatch.setattr(
        svc,
        "refresh_agreement_from_leegality",
        MagicMock(return_value={"status": "completed"}),
    )

    result = svc.apply_webhook_payload(
        {
            "documentId": "doc1",
            "mac": "ok",
            "documentStatus": "Completed",
            "webhookType": "Success",
            "request": {"action": "Signed"},
        }
    )
    assert result["ok"] is True
    assert result["agreement_id"] == 42
    assert result["action"] == "completed"
    svc.refresh_agreement_from_leegality.assert_called_once_with(agreement)


def test_apply_signed_statuses_promotes_lead():
    from services.leegality_service import _apply_signed_statuses

    lead = SimpleNamespace(id=7, status="agreement_sent")
    agreement = SimpleNamespace(
        id=42, status="sent", signed_date=None, lead=lead, lead_id=7
    )
    _apply_signed_statuses(agreement)
    assert agreement.status == "signed"
    assert agreement.signed_date is not None
    assert lead.status == "agreement_signed"


def test_apply_signed_statuses_does_not_regress_onboarding():
    from services.leegality_service import _apply_signed_statuses

    lead = SimpleNamespace(id=7, status="onboarding_started")
    agreement = SimpleNamespace(
        id=42, status="sent", signed_date=None, lead=lead, lead_id=7
    )
    _apply_signed_statuses(agreement)
    assert agreement.status == "signed"
    assert lead.status == "onboarding_started"


def test_client_sign_url_prefers_matching_client():
    from services.leegality_service import _client_sign_url

    data = {
        "invitees": [
            {
                "name": "Anshul Khare",
                "email": "anshul@example.com",
                "signUrl": "https://app1.leegality.com/sign/firm",
            },
            {
                "name": "Client Name",
                "email": "client@example.com",
                "signUrl": "https://app1.leegality.com/sign/client",
                "active": False,
            },
        ]
    }
    url = _client_sign_url(data, {"name": "Client Name", "email": "client@example.com"})
    assert url == "https://app1.leegality.com/sign/client"


def test_build_invitees_firm_first(monkeypatch):
    from services import leegality_service as svc

    monkeypatch.setattr(
        svc,
        "_cfg",
        lambda key, default="": {
            "LEEGALITY_FIRM_SIGNER_NAME": "Anshul Khare",
            "LEEGALITY_FIRM_SIGNER_EMAIL": "anshul@example.com",
            "LEEGALITY_FIRM_SIGNER_PHONE": "9881127924",
            "LEEGALITY_INVITEE_ORDER": "firm_first",
        }.get(key, default),
    )
    lead = SimpleNamespace(name="Client", email="client@example.com", phone="9876543210", client=None)
    agreement = SimpleNamespace(
        lead=lead,
        variables=[
            SimpleNamespace(variable_name="client", variable_value="Client"),
            SimpleNamespace(variable_name="client_email", variable_value="client@example.com"),
            SimpleNamespace(variable_name="client_mobile", variable_value="9876543210"),
        ],
        agreement_data="{}",
    )
    invitees, plan = svc.build_invitees_for_send(agreement, lead)
    assert plan["order"] == "firm_first"
    assert invitees[0]["email"] == "anshul@example.com"
    assert invitees[1]["email"] == "client@example.com"


def test_build_invitees_requires_firm_for_default_order(monkeypatch):
    from services import leegality_service as svc
    from services.leegality_service import LeegalityError

    monkeypatch.setattr(svc, "_cfg", lambda key, default="": default)
    lead = SimpleNamespace(name="Client", email="c@x.com", phone="9876543210", client=None)
    agreement = SimpleNamespace(
        lead=lead,
        variables=[
            SimpleNamespace(variable_name="client", variable_value="Client"),
            SimpleNamespace(variable_name="client_email", variable_value="c@x.com"),
            SimpleNamespace(variable_name="client_mobile", variable_value="9876543210"),
        ],
        agreement_data="{}",
    )
    with pytest.raises(LeegalityError, match="LEEGALITY_FIRM_SIGNER"):
        svc.build_invitees_for_send(agreement, lead)


def test_assert_client_invited_raises():
    from services.leegality_service import LeegalityError, _assert_client_invited

    with pytest.raises(LeegalityError, match="only created 1 invitee"):
        _assert_client_invited(
            {"invitees": [{"name": "Anshul Khare", "email": "a@x.com"}]},
            {"name": "Client", "email": "c@x.com"},
        )

    with pytest.raises(LeegalityError, match="not among Leegality invitees"):
        _assert_client_invited(
            {
                "invitees": [
                    {"name": "Anshul Khare", "email": "a@x.com"},
                    {"name": "Other", "email": "o@x.com"},
                ]
            },
            {"name": "Client", "email": "c@x.com"},
        )

    # Happy path: Anshul + client both present
    _assert_client_invited(
        {
            "invitees": [
                {"name": "Anshul Khare", "email": "a@x.com"},
                {"name": "Client", "email": "c@x.com"},
            ]
        },
        {"name": "Client", "email": "c@x.com"},
    )


def test_normalize_status_payload_from_transaction():
    from services.leegality_service import _normalize_status_payload

    details = _normalize_status_payload(
        {
            "documentId": "DOC1",
            "files": ["https://cdn.example/file.pdf"],
            "requests": [
                {"name": "A", "signed": True, "rejected": False, "expired": False},
                {"name": "B", "signed": True, "rejected": False, "expired": False},
            ],
        }
    )
    assert details["documentStatus"] == "Completed"
    assert details["file"] == "https://cdn.example/file.pdf"
