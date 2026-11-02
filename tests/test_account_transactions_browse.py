"""Unit checks for account-management transaction browse helpers."""

from datetime import date

import pytest

from services.account_statement_export_service import resolve_period

pytestmark = pytest.mark.no_app


def test_resolve_period_presets_for_browse_quick_links():
    today = date(2026, 11, 2)
    assert resolve_period("last_90_days", today=today) is not None
    assert resolve_period("last_12_months", today=today) is not None
    assert resolve_period("fy_current", today=today) is not None


def test_browse_helpers_importable():
    from services.account_statement_export_service import (
        browse_transactions_for_user,
        user_transaction_date_bounds,
    )

    assert callable(browse_transactions_for_user)
    assert callable(user_transaction_date_bounds)
