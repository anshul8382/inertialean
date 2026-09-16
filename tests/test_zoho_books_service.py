"""Unit tests for Zoho Books helpers (no live API / no DB)."""
from types import SimpleNamespace

import pytest

from services.zoho_books_service import (
    get_zoho_invoice_id,
    get_zoho_payment_id,
    _set_note_marker,
    _map_payment_mode,
    _STATUS_MAP,
)

pytestmark = pytest.mark.no_app


def test_zoho_id_markers_roundtrip():
    inv = SimpleNamespace(notes="")
    _set_note_marker(inv, "zoho_invoice_id", "abc123")
    assert get_zoho_invoice_id(inv) == "abc123"
    _set_note_marker(inv, "zoho_payment_id", "pay9")
    assert get_zoho_payment_id(inv) == "pay9"
    _set_note_marker(inv, "zoho_invoice_id", "xyz")
    assert get_zoho_invoice_id(inv) == "xyz"
    assert get_zoho_payment_id(inv) == "pay9"


def test_payment_mode_map():
    assert _map_payment_mode("UPI") == "banktransfer"
    assert _map_payment_mode("cash") == "cash"
    assert _map_payment_mode("") == "banktransfer"


def test_status_map_covers_paid():
    assert _STATUS_MAP["paid"] == "paid"
    assert _STATUS_MAP["overdue"] == "overdue"
