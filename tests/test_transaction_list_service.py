"""Unit tests for V2 transaction list filters and buy/sell summary."""

from pathlib import Path

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from services.transaction_list_service import (
    parse_v2_list_filters,
    summarize_trade_amounts,
)

pytestmark = pytest.mark.no_app

ROOT = Path(__file__).resolve().parents[1]


def _row(typ, qty, price):
    return SimpleNamespace(type=typ, quantity=qty, price=price)


class TestSummarizeTradeAmounts:
    def test_buy_sell_net_uses_qty_times_price(self):
        rows = [
            _row("BUY", Decimal("10"), Decimal("100")),
            _row("BUY", Decimal("2"), Decimal("50.5")),
            _row("SELL", Decimal("3"), Decimal("200")),
        ]
        out = summarize_trade_amounts(rows)
        assert out["buy_count"] == 2
        assert out["buy_amount"] == 1101.0
        assert out["sell_count"] == 1
        assert out["sell_amount"] == 600.0
        assert out["net_amount"] == 501.0
        assert out["other_count"] == 0
        assert out["transaction_count"] == 3

    def test_other_types_excluded_from_buy_sell_totals(self):
        rows = [
            _row("BUY", 1, 1000),
            _row("DIVIDEND", 10, 5),
            _row("SPLIT", 2, 0),
        ]
        out = summarize_trade_amounts(rows)
        assert out["buy_amount"] == 1000.0
        assert out["sell_amount"] == 0.0
        assert out["net_amount"] == 1000.0
        assert out["other_count"] == 2
        assert out["transaction_count"] == 3

    def test_sell_greater_than_buy_net_negative(self):
        rows = [
            ("BUY", 1, 100),
            ("SELL", 1, 400),
        ]
        out = summarize_trade_amounts(rows)
        assert out["net_amount"] == -300.0

    def test_type_is_case_insensitive(self):
        out = summarize_trade_amounts([("buy", 2, 10), ("Sell", 1, 5)])
        assert out["buy_amount"] == 20.0
        assert out["sell_amount"] == 5.0
        assert out["net_amount"] == 15.0

    def test_empty_is_zeros(self):
        out = summarize_trade_amounts([])
        assert out == {
            "transaction_count": 0,
            "buy_count": 0,
            "buy_amount": 0.0,
            "sell_count": 0,
            "sell_amount": 0.0,
            "net_amount": 0.0,
            "other_count": 0,
        }


class TestParseV2ListFilters:
    def test_parses_dates_type_and_security(self):
        filters, err = parse_v2_list_filters(
            {
                "transaction_type": " BUY ",
                "security_id": "42",
                "date_from": "2024-01-15",
                "date_to": "2024-02-01",
            }
        )
        assert err is None
        assert filters["transaction_type"] == "BUY"
        assert filters["security_id"] == 42
        assert filters["date_from"] == date(2024, 1, 15)
        assert filters["date_to"] == date(2024, 2, 1)

    def test_invalid_date_returns_error(self):
        filters, err = parse_v2_list_filters({"date_from": "15-01-2024"})
        assert filters == {}
        assert "date_from" in err

    def test_invalid_security_id_returns_error(self):
        _, err = parse_v2_list_filters({"security_id": "abc"})
        assert err == "Invalid security_id"

    def test_blank_args_are_unfiltered(self):
        filters, err = parse_v2_list_filters({})
        assert err is None
        assert filters["transaction_type"] == ""
        assert filters["security_id"] is None
        assert filters["date_from"] is None
        assert filters["date_to"] is None

    def test_type_is_uppercased(self):
        filters, err = parse_v2_list_filters({"transaction_type": "sell"})
        assert err is None
        assert filters["transaction_type"] == "SELL"


def test_transactions_template_wires_v2_filters_and_summary():
    html = (ROOT / "templates" / "transactions.html").read_text(encoding="utf-8")
    assert 'id="txn-filter-form"' in html
    assert 'id="security-filter"' in html
    assert 'id="type-filter"' in html
    assert 'id="trade-summary"' in html
    assert "traded-securities" in html
    assert "data-mobile-table=\"scroll\"" in html
    assert "buildV2ListUrl" in html
