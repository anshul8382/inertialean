"""Unit tests for household portfolio pure helpers."""
from datetime import date

from services.household_portfolio_service import (
    consolidate_holding_rows,
    merge_cashflows_by_date,
)


def test_merge_cashflows_by_date_sums_same_day():
    flows = [
        (date(2024, 1, 1), -100.0),
        (date(2024, 1, 1), -50.0),
        (date(2024, 2, 1), 25.0),
    ]
    merged = merge_cashflows_by_date(flows)
    assert merged == [(date(2024, 1, 1), -150.0), (date(2024, 2, 1), 25.0)]


def test_consolidate_holding_rows_merges_by_security():
    rows = [
        {
            "security_id": 1,
            "symbol": "ABC",
            "name": "ABC Ltd",
            "asset_class": "Equity",
            "quantity": 10,
            "current_value": 1000,
            "client_id": 1,
            "client_name": "Alice",
        },
        {
            "security_id": 1,
            "symbol": "ABC",
            "name": "ABC Ltd",
            "asset_class": "Equity",
            "quantity": 5,
            "current_value": 500,
            "client_id": 2,
            "client_name": "Bob",
        },
    ]
    out = consolidate_holding_rows(rows)
    assert len(out) == 1
    assert out[0]["quantity"] == 15
    assert out[0]["current_value"] == 1500
    assert len(out[0]["owners"]) == 2
