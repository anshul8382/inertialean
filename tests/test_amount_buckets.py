"""Tentative ₹ dispute buckets vs portfolio value."""
import pytest

from services.reconciliation.amount_buckets import build_amount_dispute_summary

pytestmark = pytest.mark.no_app


def test_three_buckets_marico_style():
    matched = [
        {"symbol": "INFY", "security_id": 1, "statement_qty": 100, "system_qty": 100},
        {"symbol": "TCS", "security_id": 2, "statement_qty": 50, "system_qty": 50},
    ]
    breaks = [
        {
            "symbol": "MARICO",
            "security_id": 3,
            "statement_qty": 114,
            "system_qty": 100,
            "delta": 14,
            "status": "FOUND_MISSING_RECOMMENDATION",
            "diagnosis": {
                "high_confidence": True,
                "top_hypothesis": "FOUND_MISSING_RECOMMENDATION — Jun 2026 recommendation",
            },
        },
        {
            "symbol": "XYZ",
            "security_id": 4,
            "statement_qty": 20,
            "system_qty": 0,
            "delta": 20,
            "status": "MISSING_IN_SYSTEM",
            "diagnosis": {"top_hypothesis": "UNKNOWN"},
        },
    ]
    prices = {"INFY": 1500.0, "TCS": 4000.0, "MARICO": 700.0, "XYZ": 100.0}
    out = build_amount_dispute_summary(
        matched_rows=matched,
        breaks=breaks,
        advisor_story={
            "fixes": ["MARICO"],
            "high_confidence_symbols": ["MARICO"],
        },
        prices_by_symbol=prices,
        fetch_missing_prices=False,
    )
    # Matched: 100*1500 + 50*4000 = 150_000 + 200_000 = 350_000
    assert out["matched"]["amount"] == 350_000.0
    # Found: |14| * 700 = 9_800
    assert out["found_sources"]["amount"] == 9_800.0
    # Needs: 20 * 100 = 2_000
    assert out["needs_check"]["amount"] == 2_000.0
    # Portfolio: matched + stmt on breaks = 350k + 114*700 + 20*100 = 350k + 79800 + 2000
    assert out["portfolio_value"] == 350_000.0 + 79_800.0 + 2_000.0
    assert out["matched"]["pct_of_portfolio"] is not None
    assert breaks[0]["tentative_amount"] == 9_800.0
    assert breaks[0]["amount_bucket"] == "found_sources"
    assert breaks[1]["amount_bucket"] == "needs_check"
    # % of portfolio for found sources
    pv = out["portfolio_value"]
    assert abs(out["found_sources"]["pct_of_portfolio"] - round(100 * 9800 / pv, 2)) < 0.01


def test_pct_none_without_prices():
    out = build_amount_dispute_summary(
        matched_rows=[{"symbol": "A", "statement_qty": 10}],
        breaks=[{"symbol": "B", "delta": 5, "statement_qty": 5, "status": "QTY_BREAK"}],
        prices_by_symbol={},
        fetch_missing_prices=False,
    )
    assert out["portfolio_value"] == 0.0
    assert out["matched"]["pct_of_portfolio"] is None
