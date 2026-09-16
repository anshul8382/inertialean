"""Match BUY/SELL trades to same-day cashflows without a transaction_id FK."""

from datetime import date
from decimal import Decimal

import pytest

from services.trades_missing_cashflow_report_service import (
    expected_signed_amount,
    match_trades_to_cashflows,
)

pytestmark = pytest.mark.no_app


def _tx(**kwargs):
    base = {
        "transaction_id": 1,
        "client_id": 10,
        "client_name": "Test",
        "type": "BUY",
        "symbol": "TCS",
        "quantity": 10,
        "price": Decimal("100"),
        "trade_amount": Decimal("1000"),
        "expected_cf_amount": Decimal("-1000"),
        "date": date(2026, 8, 1),
        "transaction_date": "2026-08-01",
    }
    base.update(kwargs)
    return base


def _cf(**kwargs):
    base = {
        "id": 50,
        "client_id": 10,
        "amount": Decimal("-1000"),
        "description": "BUY 10 shares of TCS",
        "date": date(2026, 8, 1),
        "type": "INFLOW",
    }
    base.update(kwargs)
    return base


def test_expected_signed_amount_buy_negative_sell_positive():
    assert expected_signed_amount("BUY", 1500) == Decimal("-1500")
    assert expected_signed_amount("SELL", 1500) == Decimal("1500")


def test_description_match_is_not_missing():
    missing = match_trades_to_cashflows([_tx()], [_cf()])
    assert missing == []


def test_amount_match_without_description_is_not_missing():
    missing = match_trades_to_cashflows(
        [_tx()],
        [_cf(description="Advisor note", amount=Decimal("-1000"))],
    )
    assert missing == []


def test_no_same_day_cashflow_is_missing():
    missing = match_trades_to_cashflows([_tx()], [])
    assert len(missing) == 1
    assert missing[0]["reason"] == "no_same_day_cashflow"
    assert missing[0]["transaction_id"] == 1


def test_bulk_net_cashflow_covers_multiple_trades():
    trades = [
        _tx(transaction_id=1, expected_cf_amount=Decimal("-600"), trade_amount=Decimal("600")),
        _tx(
            transaction_id=2,
            type="SELL",
            symbol="INFY",
            expected_cf_amount=Decimal("200"),
            trade_amount=Decimal("200"),
        ),
    ]
    cashflows = [
        _cf(
            description="Net cashflow for 2026-08-01",
            amount=Decimal("-400"),
        )
    ]
    assert match_trades_to_cashflows(trades, cashflows) == []


def test_same_day_wrong_amount_is_missing():
    missing = match_trades_to_cashflows(
        [_tx()],
        [_cf(description="SIP", amount=Decimal("-50000"))],
    )
    assert len(missing) == 1
    assert missing[0]["reason"] == "same_day_amount_mismatch"


def test_inflow_positive_amount_covers_buy():
    missing = match_trades_to_cashflows(
        [_tx()],
        [_cf(description="Advisor note", amount=Decimal("1000"), type="INFLOW")],
    )
    assert missing == []


def test_tplus1_cashflow_covers_trade():
    missing = match_trades_to_cashflows(
        [_tx()],
        [_cf(description="Advisor note", amount=Decimal("-1000"), date=date(2026, 8, 2))],
    )
    assert missing == []
