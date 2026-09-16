"""Practice analytics AUA projection helpers."""
from datetime import date

import pytest

from services.practice_analytics_service import (
    _calendar_months_range,
    _first_month_after,
    _last_n_monthly_average,
    _monthly_investment_chart_with_target_endpoint,
    build_aua_projection_to_target,
)


@pytest.mark.no_app
def test_first_month_after_june():
    assert _first_month_after(date(2026, 6, 29)) == date(2026, 7, 1)


@pytest.mark.no_app
def test_calendar_months_range_jul_2026_to_dec_2027():
    months = _calendar_months_range(date(2026, 7, 1), date(2027, 12, 1))
    assert len(months) == 18
    assert months[0][0] == "Jul 2026"
    assert months[-1][0] == "Dec 2027"


@pytest.mark.no_app
def test_last_n_monthly_average_uses_tail():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 100.0]
    assert _last_n_monthly_average(values, 6) == pytest.approx((2 + 3 + 4 + 5 + 6 + 100) / 6)


@pytest.mark.no_app
def test_aua_projection_flat_run_rate():
    proj = build_aua_projection_to_target(
        date(2026, 6, 29),
        current_aua=50_000_000.0,
        avg_monthly_investment=6_000_000.0,
        target_inr=1_000_000_000.0,
        target_end=date(2027, 12, 31),
    )
    assert proj["months_ahead"] == 18
    assert proj["projected_flow_total"] == 108_000_000.0
    assert proj["projected_aua"] == 158_000_000.0
    assert proj["gap_total"] == 842_000_000.0
    assert proj["gap_display"] == 842_000_000.0
    assert proj["required_monthly_investment"] == pytest.approx(52_777_777.78, rel=1e-4)
    assert proj["gap_monthly"] == pytest.approx(46_777_777.78, rel=1e-4)
    assert proj["on_track"] is False


@pytest.mark.no_app
def test_chart_endpoint_only_at_dec_2027():
    proj = build_aua_projection_to_target(
        date(2026, 6, 29),
        current_aua=50_000_000.0,
        avg_monthly_investment=6_000_000.0,
    )
    chart = _monthly_investment_chart_with_target_endpoint(
        {"Dec 2025": 1_000_000.0, "Jan 2026": 2_000_000.0},
        proj,
    )
    assert chart["labels"][-1] == "Dec 2027 ···"
    assert chart["completed"][-1] is None
    assert chart["projected_aum"][-1] == proj["projected_aua"]
    assert chart["projected_aum"][0] is None
    assert len([v for v in chart["gap"] if v is not None]) == 1
