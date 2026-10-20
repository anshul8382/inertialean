"""Liquid Bees interest-unit gap detection."""
from datetime import date

import pytest

from services.reconciliation.liquid_bees_interest import (
    assess_liquid_bees_gap,
    estimate_interest_units,
    is_liquid_bees_symbol,
    time_weighted_avg_qty,
)

pytestmark = pytest.mark.no_app


def test_is_liquid_bees_symbol():
    assert is_liquid_bees_symbol("LIQUIDBEES")
    assert is_liquid_bees_symbol("NSE:LIQUIDBEES")
    assert is_liquid_bees_symbol("XYZ", "Nippon India ETF Liquid Bees")
    assert not is_liquid_bees_symbol("MARICO")


def test_estimate_interest_units():
    # 1000 units × 4% × 365/365 = 40
    assert abs(estimate_interest_units(1000, 365) - 40.0) < 0.01


def test_time_weighted_avg_flat_holding():
    trades = [
        {"date": "2025-01-01", "type": "BUY", "quantity": 1000},
    ]
    avg, days, first = time_weighted_avg_qty(
        trades, as_of=date(2026, 1, 1), start=date(2025, 1, 1)
    )
    assert first == date(2025, 1, 1)
    assert days == 365
    assert abs(avg - 1000) < 0.01


def test_assess_recommends_zero_price_entry():
    # Avg ~1000 for 1y → ~40 interest units; statement ahead by 40
    trades = [{"date": "2025-01-01", "type": "BUY", "quantity": 1000}]
    pack = assess_liquid_bees_gap(
        symbol="LIQUIDBEES",
        statement_qty=1040,
        system_qty=1000,
        delta=40,
        as_of=date(2026, 1, 1),
        trades=trades,
    )
    assert pack is not None
    assert pack["matches"] is True
    assert pack["hypothesis"] == "LIQUIDBEES_INTEREST_UNITS"
    assert "BUY 40" in (pack.get("recommendation") or "")
    assert "₹0" in (pack.get("recommendation") or "") or "~₹0" in (
        pack.get("recommendation") or ""
    )


def test_assess_no_match_when_delta_far():
    trades = [{"date": "2025-01-01", "type": "BUY", "quantity": 1000}]
    pack = assess_liquid_bees_gap(
        symbol="LIQUIDBEES",
        statement_qty=1500,
        system_qty=1000,
        delta=500,
        as_of=date(2026, 1, 1),
        trades=trades,
    )
    assert pack["matches"] is False
    assert pack["hypothesis"] is None


def test_not_liquid_returns_none():
    assert (
        assess_liquid_bees_gap(
            symbol="INFY",
            statement_qty=110,
            system_qty=100,
            delta=10,
            as_of=date(2026, 1, 1),
            trades=[],
        )
        is None
    )
