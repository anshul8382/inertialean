"""Confirm maps must survive statement reload; bad DBCORP sticky maps must not."""
import pytest

from services.reconciliation.run_service import _preserve_confirmations

pytestmark = pytest.mark.no_app


def test_bad_dbcorp_sticky_yields_to_fresh_goldbees():
    old = [
        {
            "raw_name": "NIP IND ETF GOLD BEES",
            "isin": "INF204KB17I5",
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
            "isin": "INF204KB17I5",
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


def test_user_goldbees_confirm_wins_over_fresh_dbcorp():
    """Confirm → match reload must not wipe the user's GOLDBEES choice."""
    old = [
        {
            "raw_name": "NIP IND ETF GOLD BEES",
            "isin": "INF204KB17I5",
            "qty": 450,
            "security_id": 42,
            "security_symbol": "GOLDBEES",
            "status": "mapped",
            "match_method": "user_confirm",
        }
    ]
    fresh = [
        {
            "raw_name": "NIP IND ETF GOLD BEES",
            "isin": "INF204KB17I5",
            "qty": 450,
            "security_id": 99,
            "security_symbol": "DBCORP",
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


def test_abbrev_symbol_confirm_kept_when_fresh_also_unrelated():
    """HNGSNGBEES may not appear literally in 'HANGSENG BEES' — still keep user map."""
    old = [
        {
            "raw_name": "NIP IND ETF HANGSENG BEES",
            "isin": "INF732E01037",
            "security_id": 7,
            "security_symbol": "HNGSNGBEES",
            "status": "mapped",
            "match_method": "user_confirm",
        }
    ]
    fresh = [
        {
            "raw_name": "NIP IND ETF HANGSENG BEES",
            "isin": "INF732E01037",
            "security_id": 99,
            "security_symbol": "DBCORP",
            "status": "suggested",
            "match_method": "name_match",
        }
    ]
    out = _preserve_confirmations(old, fresh)
    assert out[0]["security_symbol"] == "HNGSNGBEES"
