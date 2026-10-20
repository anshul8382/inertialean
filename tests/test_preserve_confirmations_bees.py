"""Sticky confirm must not re-pin Gold Bees to DBCORP after a fresh resolve."""
import pytest

from services.reconciliation.run_service import _preserve_confirmations

pytestmark = pytest.mark.no_app


def test_preserve_keeps_fresh_goldbees_over_old_dbcorp():
    old = [
        {
            "raw_name": "NIP IND ETF GOLD BEES",
            "qty": 450,
            "security_id": 99,
            "security_symbol": "DBCORP",
            "status": "mapped",
            "match_method": "user_confirm",
        }
    ]
    fresh = [
        {
            "raw_name": "NIP IND ETF GOLD BEES",
            "qty": 450,
            "security_id": 42,
            "security_symbol": "GOLDBEES",
            "status": "suggested",
            "match_method": "name_match",
        }
    ]
    out = _preserve_confirmations(old, fresh)
    assert out[0]["security_symbol"] == "GOLDBEES"
    assert out[0]["security_id"] == 42


def test_preserve_drops_name_only_dbcorp_onto_unmapped_bees():
    old = [
        {
            "raw_name": "NIP IND ETF LIQUID BEES",
            "qty": 0.003,
            "security_id": 99,
            "security_symbol": "DBCORP",
            "status": "mapped",
            "match_method": "user_confirm",
        }
    ]
    fresh = [
        {
            "raw_name": "NIP IND ETF LIQUID BEES",
            "qty": 0.003,
            "security_id": None,
            "security_symbol": None,
            "status": "unmapped",
        }
    ]
    out = _preserve_confirmations(old, fresh)
    assert out[0].get("security_id") is None
    assert out[0].get("status") == "unmapped"
