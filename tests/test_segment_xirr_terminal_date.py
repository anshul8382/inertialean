"""
Regression / documentation tests for segment XIRR vs. calculate_xirr end_date.

`calculate_segment_wise_xirr` currently calls `calculate_xirr(cashflows, current_value)`
without `end_date=end_date`, so the terminal inflow is dated with `datetime.now()`.
For a period that ended in the past, that incorrectly stretches the discounting
horizon and can depress the annualized XIRR far below the simple (end-start)/start
return — e.g. Gold ~27% period return vs ~16% XIRR when the report is generated
months after period end.

No production code changes here; this file exists to lock expected behavior once
`end_date` is passed from segment XIRR.
"""
from datetime import date

import pytest

from api.v1.performance import calculate_xirr


def _gold_like_cashflows():
    # Order-of-magnitude from GOLDBEES / segment table (Sep 14, 2024 – May 19, 2025)
    d0 = date(2024, 9, 14)
    d_mid = date(2025, 2, 10)  # small buy during window (~₹11K)
    return [
        (d0, -588_970.20),  # start value as outflow
        (d_mid, -11_304.00),  # net incremental (matches "insignificant" inflow)
    ]


def test_xirr_gold_like_high_when_terminal_on_period_end():
    """Terminal dated at period end → annual IRR should be in a high-30s / low-40s % range."""
    cfs = _gold_like_cashflows()
    end = date(2025, 5, 19)
    current = 760_643.84
    xirr, t_inv, t_w, net, _ = calculate_xirr(cfs, current, end_date=end)
    # Not tied to an exact tick from Excel, but should not be ~16% if economics are ~+27% over ~8m
    assert xirr > 0.30, f"expected high annual XIRR with correct end_date, got {xirr:.4f} ({xirr*100:.1f}%)"
    assert xirr < 1.20  # sanity cap
    # Simple money basis check (display “Return %” in segment) — same numbers as in segment
    assert net > 0
    r_simple = (current - net) / net
    assert 0.20 < r_simple < 0.35  # ~26.7% in live data; window for test inputs


def test_xirr_gold_like_depressed_when_terminal_dated_far_after_period_end():
    """
    Same flows and terminal *amount*; if the final +PV is dated far after the period end
    (what happens when `calculate_xirr` is called without `end_date` and `datetime.now()`
    is months after the analysis period), annualized XIRR drops materially.

    We simulate that by passing `end_date` = a late report run date instead of patching
    `datetime.now` (built-in cannot be patched).
    """
    cfs = _gold_like_cashflows()
    period_end = date(2025, 5, 19)
    current = 760_643.84
    xirr_correct, _, _, _, _ = calculate_xirr(cfs, current, end_date=period_end)

    report_run_day = date(2026, 4, 25)
    xirr_late_terminal, _, _, _, _ = calculate_xirr(cfs, current, end_date=report_run_day)

    assert xirr_late_terminal < xirr_correct * 0.7, (
        f"expected late terminal date to lower IRR; correct={xirr_correct:.4f}, late={xirr_late_terminal:.4f}"
    )
    assert xirr_late_terminal < 0.25  # same order of magnitude as ~16% vs ~40% when period ended May 2025


def test_net_investment_matches_cashflow_sum():
    cfs = _gold_like_cashflows()
    xirr, total_inv, total_w, net, _ = calculate_xirr(
        cfs, 760_643.84, end_date=date(2025, 5, 19)
    )
    # net = sum(negative) - sum(positive) from flows only, *excluding* final terminal in list
    assert abs(net - 600_274.20) < 1.0  # 588970.20 + 11304


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
