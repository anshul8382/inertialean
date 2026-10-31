"""Unit tests for Zoho → Inertia invoice pull helpers (mocked API)."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.zoho_invoice_pull_service import (
    _amounts_from_zoho,
    _local_status_from_zoho_row,
    _parse_date,
)
from services.zoho_books_service import list_zoho_invoices

pytestmark = pytest.mark.no_app


def test_parse_date_iso():
    assert _parse_date("2026-04-15") == date(2026, 4, 15)
    assert _parse_date(None) is None


def test_amounts_and_status_closed_zero_balance():
    row = {
        "total": "1180.00",
        "sub_total": "1000.00",
        "tax_total": "180.00",
        "balance": "0",
        "status": "closed",
    }
    am = _amounts_from_zoho(row)
    assert am["total_amount"] == Decimal("1180.00")
    assert am["net_amount"] == Decimal("1000.00")
    assert am["tax_amount"] == Decimal("180.00")
    assert _local_status_from_zoho_row(row) == "paid"


def test_status_overdue():
    assert _local_status_from_zoho_row({"status": "overdue", "balance": "100"}) == "overdue"


def test_sync_missing_uses_create_only():
    from services import zoho_invoice_pull_service as mod

    with patch.object(mod, "pull_zoho_invoices", return_value={"ok": True, "created": 3}) as mocked:
        out = mod.sync_missing_zoho_invoices(user_id=1, date_start=date(2026, 4, 1), date_end=date(2026, 9, 30))
        assert out["ok"] is True
        kwargs = mocked.call_args.kwargs
        assert kwargs["create_only"] is True
        assert kwargs["update_existing"] is False


def test_score_zoho_contact_name_prefers_close_match():
    from services.zoho_invoice_pull_service import _score_zoho_contact_name

    assert _score_zoho_contact_name("Rahul Sharma", "Rahul Sharma") == 1.0
    assert _score_zoho_contact_name("Rahul Sharma", "Mr. Rahul Sharma") >= 0.92
    assert _score_zoho_contact_name("Rahul Sharma", "Completely Different") < 0.5


@patch("services.zoho_invoice_pull_service.zoho_configured", return_value=True)
@patch("services.zoho_invoice_pull_service.get_household_zoho_mapping")
@patch("services.zoho_invoice_pull_service.get_stored_zoho_contact_id", return_value=None)
def test_resolve_uses_household_peer_zoho_contact(mock_stored, mock_hh, _cfg):
    from services.zoho_invoice_pull_service import resolve_zoho_contact_for_client

    peer = SimpleNamespace(id=20, name="Ajith Menon", email="ajith@x.com", zoho_contact_id="Z-99")
    mock_hh.return_value = {
        "contact_id": "Z-99",
        "owner": peer,
        "owner_client_id": 20,
        "owner_name": "Ajith Menon",
        "reason": "household_peer",
        "household_name": "Menon Family",
    }
    anjali = SimpleNamespace(id=21, name="Anjali Menon", email="anjali@x.com", zoho_contact_id=None)
    out = resolve_zoho_contact_for_client(anjali)
    assert out["ok"] is True
    assert out["contact_id"] == "Z-99"
    assert out["match_reason"] == "household_peer"
    assert out["mapped_client_id"] == 20


@patch("services.zoho_invoice_pull_service.list_household_peer_clients", return_value=[])
@patch("services.zoho_invoice_pull_service.pull_zoho_invoices", return_value={"ok": True, "created": 1})
@patch("services.zoho_invoice_pull_service.resolve_zoho_contact_for_client")
def test_pull_for_client_forces_mapped_household_owner(mock_resolve, mock_pull, _peers):
    from services.zoho_invoice_pull_service import pull_zoho_invoices_for_client

    mock_resolve.return_value = {
        "ok": True,
        "contact_id": "Z-99",
        "match_reason": "household_peer",
        "mapped_client_id": 20,
        "mapped_client_name": "Ajith Menon",
    }
    anjali = SimpleNamespace(id=21, name="Anjali Menon")
    out = pull_zoho_invoices_for_client(anjali, user_id=1)
    assert out["ok"] is True
    kwargs = mock_pull.call_args.kwargs
    assert kwargs["force_client_id"] == 20
    assert kwargs["zoho_customer_id"] == "Z-99"
    assert 21 in kwargs["accessible_client_ids"]
    assert 20 in kwargs["accessible_client_ids"]


@patch("services.zoho_invoice_pull_service.zoho_configured", return_value=True)
@patch("services.zoho_invoice_pull_service.find_zoho_contact_by_email", return_value=(None, None))
@patch("services.zoho_invoice_pull_service.list_zoho_contacts")
def test_suggest_zoho_contacts_ranks_closest(mock_list, _email, _cfg):
    from services.zoho_invoice_pull_service import suggest_zoho_contacts_for_client

    mock_list.return_value = (
        [
            {"contact_id": "1", "contact_name": "Ramesh Kumar", "email": "a@x.com"},
            {"contact_id": "2", "contact_name": "Rahul Sharma", "email": "r@x.com"},
            {"contact_id": "3", "contact_name": "Other Person", "email": "o@x.com"},
        ],
        None,
    )
    client = SimpleNamespace(id=1, name="Rahul Sharma", email="no-match@example.com")
    out = suggest_zoho_contacts_for_client(client, limit=5)
    assert out["ok"] is True
    assert out["best"]["contact_id"] == "2"
    assert out["best"]["score"] >= 0.9



@patch("services.zoho_books_service.zoho_configured", return_value=True)
@patch("services.zoho_books_service._request")
def test_list_zoho_invoices_paginates(mock_req, _cfg):
    mock_req.side_effect = [
        (
            {
                "invoices": [{"invoice_id": "1", "invoice_number": "INV-1"}],
                "page_context": {"has_more_page": True},
            },
            None,
        ),
        (
            {
                "invoices": [{"invoice_id": "2", "invoice_number": "INV-2"}],
                "page_context": {"has_more_page": False},
            },
            None,
        ),
    ]
    # list_zoho_invoices needs app config path via zoho_configured already mocked;
    # _request is mocked so no real HTTP.
    rows, err = list_zoho_invoices(
        date_start=date(2026, 4, 1), date_end=date(2026, 9, 30), per_page=1
    )
    assert err is None
    assert len(rows) == 2
    assert mock_req.call_count == 2


@patch("services.zoho_invoice_pull_service.zoho_configured", return_value=True)
@patch("services.zoho_invoice_pull_service.list_zoho_invoices")
@patch("services.zoho_invoice_pull_service.suggest_client_match")
@patch("services.zoho_invoice_pull_service.resolve_agreement_id_for_client", return_value=None)
@patch("services.zoho_invoice_pull_service._find_by_zoho_id", return_value=None)
def test_pull_creates_matched_skips_unmatched(
    _find, _agr, mock_match, mock_list, _cfg, app
):
    from services.zoho_invoice_pull_service import pull_zoho_invoices
    from models import Invoice
    from extensions import db

    mock_list.return_value = (
        [
            {
                "invoice_id": "z1",
                "invoice_number": "INV-PULL-TEST-1",
                "date": "2026-05-01",
                "due_date": "2026-05-15",
                "status": "paid",
                "total": "100.00",
                "sub_total": "100.00",
                "balance": "0",
                "customer_name": "Test Client Pull",
                "email": "pull-test@example.com",
            },
            {
                "invoice_id": "z2",
                "invoice_number": "INV-PULL-TEST-2",
                "date": "2026-05-02",
                "status": "sent",
                "total": "50.00",
                "customer_name": "Unknown Zoho Person XYZ",
            },
        ],
        None,
    )

    def match_side(name, email, ids):
        if email == "pull-test@example.com":
            return 1, "Test Client Pull", "email", []
        return None, None, "none", []

    mock_match.side_effect = match_side

    with app.app_context():
        # Ensure client 1 exists or use a real client id from fixtures
        from models import Client, User

        client = Client.query.first()
        user = User.query.filter_by(is_admin=True).first() or User.query.first()
        if not client or not user:
            pytest.skip("needs client + user in test DB")

        def match_side2(name, email, ids):
            if email == "pull-test@example.com":
                return client.id, client.name, "email", []
            return None, None, "none", []

        mock_match.side_effect = match_side2

        # Clean prior test rows
        for num in ("INV-PULL-TEST-1", "INV-PULL-TEST-2"):
            old = Invoice.query.filter_by(invoice_number=num).first()
            if old:
                db.session.delete(old)
        db.session.commit()

        result = pull_zoho_invoices(
            date_start=date(2026, 4, 1),
            date_end=date(2026, 9, 30),
            user_id=user.id,
            accessible_client_ids=None,
            update_existing=True,
        )
        assert result["ok"] is True
        assert result["created"] == 1
        assert len(result["unmatched"]) == 1
        inv = Invoice.query.filter_by(invoice_number="INV-PULL-TEST-1").first()
        assert inv is not None
        assert inv.status == "paid"
        assert "[zoho_invoice_id=z1]" in (inv.notes or "")
        db.session.delete(inv)
        db.session.commit()
