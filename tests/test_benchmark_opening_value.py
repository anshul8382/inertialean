"""Nifty / period opening must match XIRR: cashflows-only before first CF day."""

from datetime import date

from api.v1.period_analysis import resolve_period_opening_value


def test_lifetime_opening_zero_when_start_on_or_before_first_cashflow():
    reconstructed = 1_170_055.50  # Leena-style seed holdings MTM
    first_cf = date(2022, 1, 4)
    assert resolve_period_opening_value(reconstructed, date(2022, 1, 3), first_cf) == 0.0
    assert resolve_period_opening_value(reconstructed, date(2022, 1, 4), first_cf) == 0.0


def test_mid_period_opening_uses_reconstructed_mtm():
    reconstructed = 2_500_000.0
    first_cf = date(2022, 1, 4)
    assert (
        resolve_period_opening_value(reconstructed, date(2025, 10, 1), first_cf)
        == reconstructed
    )


def test_no_cashflows_keeps_reconstructed():
    reconstructed = 100_000.0
    assert resolve_period_opening_value(reconstructed, date(2022, 1, 3), None) == reconstructed


def test_zero_reconstructed_stays_zero():
    assert resolve_period_opening_value(0.0, date(2022, 1, 3), date(2022, 1, 4)) == 0.0
