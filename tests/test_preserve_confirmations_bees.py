"""User-owned holdings maps are overwritten only by a later user confirm."""
import pytest

from services.reconciliation.run_service import _preserve_confirmations

pytestmark = pytest.mark.no_app


def test_user_goldbees_confirm_never_overwritten_by_fresh_dbcorp():
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
    assert out[0]["match_method"] == "user_confirm"


def test_user_dbcorp_confirm_also_sticky_until_user_changes():
    """Even a wrong user confirm stays until the user remaps — not auto-fixed."""
    old = [
        {
            "raw_name": "NIP IND ETF GOLD BEES",
            "isin": "INF204KB17I5",
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
            "security_id": 42,
            "security_symbol": "GOLDBEES",
            "status": "suggested",
            "match_method": "name_match",
        }
    ]
    out = _preserve_confirmations(old, fresh)
    assert out[0]["security_symbol"] == "DBCORP"


def test_auto_name_match_may_be_replaced_by_fresh():
    old = [
        {
            "raw_name": "NIP IND ETF GOLD BEES",
            "security_id": 99,
            "security_symbol": "DBCORP",
            "status": "suggested",
            "match_method": "name_match",
        }
    ]
    fresh = [
        {
            "raw_name": "NIP IND ETF GOLD BEES",
            "security_id": 42,
            "security_symbol": "GOLDBEES",
            "status": "suggested",
            "match_method": "name_match",
        }
    ]
    out = _preserve_confirmations(old, fresh)
    assert out[0]["security_symbol"] == "GOLDBEES"


def test_preserved_confirm_stays_user_owned():
    old = [
        {
            "raw_name": "NIP IND ETF LIQUID BEES",
            "isin": "INF204KB19I1",
            "security_id": 55,
            "security_symbol": "LIQUIDBEES",
            "status": "mapped",
            "match_method": "preserved_confirm",
        }
    ]
    fresh = [
        {
            "raw_name": "NIP IND ETF LIQUID BEES",
            "isin": "INF204KB19I1",
            "security_id": 99,
            "security_symbol": "DBCORP",
            "status": "suggested",
            "match_method": "name_match",
        }
    ]
    out = _preserve_confirmations(old, fresh)
    assert out[0]["security_symbol"] == "LIQUIDBEES"
    assert out[0]["match_method"] == "user_confirm"
