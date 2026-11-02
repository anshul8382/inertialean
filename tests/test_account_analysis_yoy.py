"""Same-period last-year helpers for account management analysis."""

from datetime import date

import pytest

from services.account_statement_export_service import (
    build_yoy_head_rows,
    prior_year_period,
)

pytestmark = pytest.mark.no_app


def test_prior_year_period_shifts_fy_window():
    # Current FY Q1 Apr–Jun 2026 → prior FY Q1 Apr–Jun 2025
    p_from, p_to, label = prior_year_period(date(2026, 4, 1), date(2026, 6, 30))
    assert p_from == date(2025, 4, 1)
    assert p_to == date(2025, 6, 30)
    assert "2025" in label


def test_prior_year_period_handles_leap_day():
    p_from, p_to, _ = prior_year_period(date(2024, 2, 29), date(2024, 3, 1))
    assert p_from == date(2023, 2, 28)
    assert p_to == date(2023, 3, 1)


def test_build_yoy_head_rows_merges_and_marks_new():
    current = [
        {"head_id": 1, "name": "Salary", "head_type": "credit", "total": 100.0, "count": 2},
        {"head_id": 2, "name": "Interest", "head_type": "credit", "total": 10.0, "count": 1},
    ]
    prior = [
        {"head_id": 1, "name": "Salary", "head_type": "credit", "total": 80.0, "count": 2},
        {"head_id": 3, "name": "Gift", "head_type": "credit", "total": 5.0, "count": 1},
    ]
    rows = build_yoy_head_rows(current, prior)
    by_name = {r["name"]: r for r in rows}
    assert by_name["Salary"]["delta"] == pytest.approx(20.0)
    assert by_name["Salary"]["pct_change"] == pytest.approx(25.0)
    assert by_name["Interest"]["is_new"] is True
    assert by_name["Gift"]["is_gone"] is True
