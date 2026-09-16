"""PC2 audit helpers — entry vs period-start start price (no DB)."""
from __future__ import annotations

import pytest

from services.portfolio_review_audit_engine import (
    _pc2_holder_meta_by_symbol,
    _pc2_uses_entry_start_price,
    _pc2_verify_entry_start_price,
    extract_holdings,
)

pytestmark = pytest.mark.no_app


def test_extract_holdings_parses_basis_suffix():
    raw = "Holdings (SYMBOL  START_PRICE  END_PRICE  RETURN%):\n"
    raw += "NEWCO  80.00  90.00  12.50%  basis=period_purchase_vwap\n"
    rows = extract_holdings(raw)
    assert len(rows) == 1
    assert rows[0]["name"] == "NEWCO"
    assert rows[0]["return_basis"] == "period_purchase_vwap"


def test_pc2_holder_meta_from_period_analysis():
    pa = {
        "portfolio_holdings_comparison": [
            {
                "symbol": "ABC",
                "return_basis": "blended",
                "period_start_market_price": 100.0,
                "period_buy_vwap": 80.0,
                "start": {"quantity": 100},
            }
        ]
    }
    meta = _pc2_holder_meta_by_symbol(pa)
    assert meta["ABC"]["return_basis"] == "blended"
    assert meta["ABC"]["period_buy_vwap"] == 80.0


def test_pc2_blended_skips_market_close_red():
    arith, red, amber = [], [], []
    _pc2_verify_entry_start_price(
        "XYZ",
        93.33,
        {
            "return_basis": "blended",
            "period_start_market_price": 100.0,
            "period_buy_vwap": 80.0,
        },
        arith,
        red,
        amber,
    )
    assert red == []
    assert any("blended" in line for line in arith)


def test_pc2_purchase_vwap_within_tolerance_is_green():
    arith, red, amber = [], [], []
    _pc2_verify_entry_start_price(
        "XYZ",
        80.0,
        {"return_basis": "period_purchase_vwap", "period_buy_vwap": 80.0},
        arith,
        red,
        amber,
    )
    assert red == []
    assert any("GREEN" in line for line in arith)


def test_pc2_uses_entry_start_price_flags():
    assert not _pc2_uses_entry_start_price("period_start_market")
    assert _pc2_uses_entry_start_price("period_purchase_vwap")
    assert _pc2_uses_entry_start_price("blended")
