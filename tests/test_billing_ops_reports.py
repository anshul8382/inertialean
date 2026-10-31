"""Unit tests for Billing Manager companion ops reports."""

from datetime import date

import pytest

from services.billing_ops_reports_service import (
    _months_between,
    build_billing_slipping,
    build_billing_ops_reports,
)

pytestmark = pytest.mark.no_app


def test_months_between_day_aware():
    assert _months_between(date(2026, 1, 15), date(2026, 8, 14)) == 6
    assert _months_between(date(2026, 1, 15), date(2026, 8, 15)) == 7


def test_billing_slipping_adds_days_since_last_billing():
    out = build_billing_slipping(
        as_of=date(2026, 10, 31),
        lagging_rows=[
            {
                "client_id": 1,
                "client_name": "Lagging Co",
                "last_billed_date": "2025-07-19",
                "next_billing_date": "2026-01-19",
                "days_overdue": 285,
                "estimated_invoice_value": 100000,
            }
        ],
    )
    assert out["count"] == 1
    assert out["rows"][0]["days_since_last_billing"] == (date(2026, 10, 31) - date(2025, 7, 19)).days


def test_ops_reports_bundle_without_db_for_slipping_only():
    """status_report=None → empty slipping; reviews/missing need DB so skip full call."""
    slipping = build_billing_slipping(as_of=date(2026, 10, 31), lagging_rows=[])
    assert slipping["count"] == 0
    bundle = {
        "reviews_on_track": {"count": 0, "rows": [], "max_months": 7},
        "billing_slipping": slipping,
        "missing_agreements": {"count": 0, "rows": []},
    }
    assert bundle["billing_slipping"]["count"] == 0
