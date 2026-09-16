"""Tests for asset class warning dismissal."""
from services.asset_class_audit_service import (
    asset_class_warnings_fingerprint,
    dismiss_asset_class_warning_for_session,
    is_asset_class_warning_dismissed,
)


class _FakeSession(dict):
    modified = False


def test_fingerprint_changes_when_warnings_change():
    w1 = [{'security_id': 1, 'symbol': 'ABC', 'stored_class': 'Equity', 'suggested_class': 'Equity ETF'}]
    w2 = w1 + [{'security_id': 2, 'symbol': 'XYZ', 'stored_class': 'Debt', 'suggested_class': 'Fixed Income'}]
    assert asset_class_warnings_fingerprint(w1) != asset_class_warnings_fingerprint(w2)


def test_dismiss_hides_same_fingerprint_only():
    warnings = [{'security_id': 5, 'symbol': 'ETF1', 'stored_class': 'Equity', 'suggested_class': 'Equity ETF'}]
    fp = asset_class_warnings_fingerprint(warnings)
    sess = _FakeSession()
    assert is_asset_class_warning_dismissed(sess, 42, fp) is False
    dismiss_asset_class_warning_for_session(sess, 42, fp)
    assert is_asset_class_warning_dismissed(sess, 42, fp) is True
    assert is_asset_class_warning_dismissed(sess, 99, fp) is False
    assert is_asset_class_warning_dismissed(sess, 42, fp + 'x') is False
