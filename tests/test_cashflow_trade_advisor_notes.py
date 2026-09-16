"""Unit tests for advisor spreadsheet notes parse + match helpers."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.cashflow_trade_advisor_notes_service import (
    LAKH_TO_INR,
    match_tolerance,
    parse_advisor_notes_paste,
)
from services.cashflow_trade_mismatch_report_service import (
    resolve_effective_reconcile_start,
    split_cashflows_at_reconcile_start,
)

pytestmark = pytest.mark.no_app


def test_parse_tsv_with_header_lakhs():
    text = "Date\tAmount\n2019-03-15\t-2.5\n2020-01-10\t1.0\n"
    parsed = parse_advisor_notes_paste(text)
    assert parsed["ok"] is True
    assert parsed["row_count"] == 2
    assert parsed["reconciled_through"] == "2020-01-10"
    assert parsed["advisor_net_inr"] == pytest.approx((-2.5 + 1.0) * LAKH_TO_INR)
    assert parsed["rows"][0]["amount_inr"] == pytest.approx(-2.5 * LAKH_TO_INR)


def test_parse_csv_dmy():
    text = "15/03/2019,-1.5\n10/01/2020,0.5\n"
    parsed = parse_advisor_notes_paste(text)
    assert parsed["ok"] is True
    assert parsed["row_count"] == 2
    assert parsed["reconciled_through"] == "2020-01-10"


def test_parse_empty():
    assert parse_advisor_notes_paste("").get("ok") is False


def test_match_tolerance_floor():
    assert match_tolerance(0) == 1000.0
    assert match_tolerance(500_000) == 5000.0


def test_adjacent_day_booking_lag_not_flagged():
    """System on D, advisor on D+1 with near-equal amounts → not a day gap."""
    from services.cashflow_trade_advisor_notes_service import _day_gaps_after_adjacent_match

    gaps = _day_gaps_after_adjacent_match(
        {"2025-12-11": -149_000.0},
        {"2025-12-10": -149_412.0},
    )
    assert gaps == []


def test_canonical_inflow_positive_equals_negative_note():
    from services.cashflow_service import canonical_cashflow_signed_amount

    assert canonical_cashflow_signed_amount({"amount": 149_000, "type": "INFLOW"}) == -149_000.0
    assert canonical_cashflow_signed_amount({"amount": -149_000, "type": "INFLOW"}) == -149_000.0


def test_canonical_outflow_negative_keeps_stored_sign():
    """Mistagged BUY cashflows: OUTFLOW + negative amount must not flip to +."""
    from services.cashflow_service import canonical_cashflow_signed_amount

    assert canonical_cashflow_signed_amount({"amount": -6584.64, "type": "OUTFLOW"}) == -6584.64
    assert canonical_cashflow_signed_amount({"amount": 5000.0, "type": "OUTFLOW"}) == 5000.0
    assert canonical_cashflow_signed_amount({"amount": -6584.64, "type": "OUTFLOW"}) + canonical_cashflow_signed_amount(
        {"amount": -7341.40, "type": "OUTFLOW"}
    ) == pytest.approx(-13926.04)


def test_split_system_rows_clubbed_to_one_note():
    """One advisor note vs two nearby system rows that sum within club band."""
    from services.cashflow_trade_advisor_notes_service import _day_gaps_after_adjacent_match

    gaps = _day_gaps_after_adjacent_match(
        {"2024-08-28": -150_000.0},
        {"2024-08-27": -50_782.0, "2024-08-29": -97_635.0},
    )
    assert gaps == []


def test_far_apart_same_amount_still_flagged():
    from services.cashflow_trade_advisor_notes_service import _day_gaps_after_adjacent_match

    gaps = _day_gaps_after_adjacent_match(
        {"2024-01-01": -100_000.0},
        {"2024-02-01": -100_000.0},  # >14 days
    )
    assert len(gaps) == 2


def test_true_same_day_mismatch_still_flagged():
    from services.cashflow_trade_advisor_notes_service import _day_gaps_after_adjacent_match

    gaps = _day_gaps_after_adjacent_match(
        {"2024-08-28": -150_000.0},
        {"2024-08-28": 0.0},
    )
    assert len(gaps) == 1
    assert gaps[0]["date"] == "2024-08-28"


def test_split_at_reconcile_without_opening_book():
    cfs = [
        SimpleNamespace(date=date(2019, 1, 1), amount=-100),
        SimpleNamespace(date=date(2021, 1, 1), amount=-50),
    ]
    pre, post = split_cashflows_at_reconcile_start(
        cfs, reconcile_start=date(2020, 1, 1), has_opening_book=True
    )
    assert len(pre) == 1 and len(post) == 1


def test_effective_reconcile_start_notes_only():
    with patch(
        "services.cashflow_trade_mismatch_report_service._advisor_notes_reconcile_bounds",
        return_value=(date(2020, 6, 30), date(2020, 7, 1)),
    ):
        start, through, use_epoch = resolve_effective_reconcile_start(
            1, opening_reconcile_start=None, has_opening_book=False
        )
    assert through == date(2020, 6, 30)
    assert start == date(2020, 7, 1)
    assert use_epoch is True


def test_health_full_match():
    from services.cashflow_trade_integrity_case_service import build_calculation_health

    h = build_calculation_health(
        status="matched",
        has_opening_book=False,
        series_matched=True,
    )
    assert h["level"] == "full_accurate"
    assert "accurate" in h["headline"].lower()


def test_health_opening_book_matched():
    from services.cashflow_trade_integrity_case_service import build_calculation_health

    h = build_calculation_health(
        status="ignore_opening_book",
        has_opening_book=True,
        first_real_trade_date="2019-06-01",
        series_matched=True,
    )
    assert h["level"] == "post_real_trades_accurate"
    assert "2019-06-01" in h["summary"]


def test_health_notes_and_trades():
    from services.cashflow_trade_integrity_case_service import build_calculation_health

    h = build_calculation_health(
        status="matched",
        has_opening_book=False,
        advisor_notes_through="2020-06-30",
        trade_reconcile_from="2020-07-01",
        series_matched=True,
    )
    assert h["level"] == "hybrid_notes_and_trades"
    assert "2020-07-01" in h["headline"]
    assert any(p["basis"] == "advisor_notes" for p in h["periods"])
    assert any(p["basis"] == "cashflow_vs_trades" for p in h["periods"])


def test_health_notes_ok_post_open():
    from services.cashflow_trade_integrity_case_service import build_calculation_health

    h = build_calculation_health(
        status="mismatch_material",
        has_opening_book=True,
        first_real_trade_date="2018-01-01",
        advisor_notes_through="2021-12-31",
        trade_reconcile_from="2022-01-01",
    )
    assert h["level"] == "notes_ok_post_open"
    assert h["tone"] == "warning"
