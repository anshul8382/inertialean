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
