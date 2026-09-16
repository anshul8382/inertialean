"""Unit tests for advisory AUA exclusion helpers (no DB)."""

import pytest

from services.holding_advisory_scope_service import (
    filter_portfolio_holdings,
    holding_security_id,
    sum_holding_values,
)

pytestmark = pytest.mark.no_app


def test_holding_security_id_from_dict():
    assert holding_security_id({"security_id": 42}) == 42
    assert holding_security_id({"symbol": "X"}) is None


def test_filter_portfolio_holdings_splits_included_and_excluded():
    holdings = [
        {"security_id": 1, "symbol": "AAA", "current_value": 1000},
        {"security_id": 2, "symbol": "BBB", "current_value": 500},
        {"security_id": 3, "symbol": "CCC", "current_value": 200},
    ]
    included, excluded = filter_portfolio_holdings(holdings, {2})
    assert len(included) == 2
    assert len(excluded) == 1
    assert excluded[0]["symbol"] == "BBB"
    assert sum_holding_values(included) == 1200.0
    assert sum_holding_values(excluded) == 500.0


def test_filter_portfolio_holdings_empty_exclusions():
    holdings = [{"security_id": 1, "value": 100}]
    included, excluded = filter_portfolio_holdings(holdings, set())
    assert included == holdings
    assert excluded == []
