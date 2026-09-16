"""Unit tests for utils.security_asset_class (DB truth + audit suggestions)."""
from types import SimpleNamespace

from utils.security_asset_class import (
    asset_classes_equivalent,
    check_asset_class_mismatch,
    derive_asset_class,
    suggest_asset_class,
)


def _sec(**kwargs):
    defaults = {
        'id': 1,
        'name': '',
        'symbol': '',
        'security_type': '',
        'meta_data': None,
        'asset_class': None,
    }
    defaults.update(kwargs)
    ac = defaults.pop('asset_class', None)
    if isinstance(ac, str):
        ac = SimpleNamespace(name=ac)
    return SimpleNamespace(asset_class=ac, **defaults)


def test_derive_uses_db_only_equity_for_mislabeled_etf():
    sec = _sec(name='Nippon India ETF Nifty BeES', symbol='NIFTYBEES', security_type='ETF', asset_class='Equity')
    assert derive_asset_class(sec) == 'Equity'


def test_suggest_flags_equity_etf_for_same_security():
    sec = _sec(name='Nippon India ETF Nifty BeES', symbol='NIFTYBEES', security_type='ETF', asset_class='Equity')
    assert suggest_asset_class(sec) == 'Equity ETF'


def test_mismatch_when_stored_differs_from_suggestion():
    sec = _sec(name='Nippon India ETF Nifty BeES', symbol='NIFTYBEES', security_type='ETF', asset_class='Equity')
    row = check_asset_class_mismatch(sec)
    assert row is not None
    assert row['stored_class'] == 'Equity'
    assert row['suggested_class'] == 'Equity ETF'


def test_no_mismatch_when_db_already_equity_etf():
    sec = _sec(name='Nippon India ETF Nifty BeES', symbol='NIFTYBEES', security_type='ETF', asset_class='Equity ETF')
    assert check_asset_class_mismatch(sec) is None


def test_debt_fixed_income_aliases_not_mismatch():
    assert asset_classes_equivalent('Debt', 'Fixed Income') is True
    sec = _sec(name='Bharat Bond ETF', symbol='BBETF', security_type='ETF', asset_class='Debt')
    assert check_asset_class_mismatch(sec) is None


def test_direct_equity_no_mismatch():
    sec = _sec(name='Reliance Industries', symbol='RELIANCE', security_type='STOCK', asset_class='Equity')
    assert derive_asset_class(sec) == 'Equity'
    assert check_asset_class_mismatch(sec) is None
