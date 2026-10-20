"""Advisor holdings paste parse / drift."""
import pytest

from datetime import date

from services.reconciliation.advisor_holdings_notes_service import (
    compare_to_system,
    compute_sync_through,
    parse_holdings_paste,
)

pytestmark = pytest.mark.no_app

SAMPLE = """Company Name	NSE Symbol	Quantity	Purchase Price	Purchase Value	Current Price	Current Value	Return	Weight
Affle (India) Ltd	NSE:AFFLE	50	405	20,260	1,445	72,250	257%	1.9%
AU Small Finance Bank	NSE:AUBANK	126	379	47,778	972	122,434	156%	3.2%
Bajaj Finance	NSE:BAJFINANCE	220	366	80,596	957	210,617	161%	5.6%
"""


def test_parse_holdings_paste_strips_nse_prefix():
    out = parse_holdings_paste(SAMPLE)
    assert out["ok"] is True
    assert len(out["rows"]) >= 3
    syms = {r["symbol"] for r in out["rows"]}
    assert "AFFLE" in syms
    assert "AUBANK" in syms
    affle = next(r for r in out["rows"] if r["symbol"] == "AFFLE")
    assert affle["quantity"] == 50
    assert affle["current_price"] == 1445


def test_compare_to_system_drift():
    rows = [{"symbol": "AFFLE", "quantity": 50}, {"symbol": "TCS", "quantity": 10}]
    drift = compare_to_system(rows, {"AFFLE": 50, "TCS": 7})
    assert len(drift) == 1
    assert drift[0]["symbol"] == "TCS"
    assert drift[0]["delta"] == 3


def test_compute_sync_through_max():
    assert compute_sync_through(
        as_of=date(2026, 1, 1),
        reconciled_through=date(2025, 12, 15),
        last_trade_date=date(2026, 2, 1),
    ) == date(2026, 2, 1)
