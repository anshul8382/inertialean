"""Tests for portfolio asset-class display ordering."""
from utils.portfolio_asset_class_display import (
    EQUITY_MUTUAL_FUNDS,
    asset_class_display_rank,
    is_mutual_fund_security_type,
    sort_asset_class_keys,
)


def test_sort_puts_equity_mf_after_equity():
    keys = ["Gold", EQUITY_MUTUAL_FUNDS, "Equity", "Fixed Income"]
    assert sort_asset_class_keys(keys) == [
        "Equity",
        EQUITY_MUTUAL_FUNDS,
        "Fixed Income",
        "Gold",
    ]


def test_debt_alias_ranks_with_fixed_income():
    assert asset_class_display_rank("Debt") == asset_class_display_rank("Fixed Income")


def test_mutual_fund_security_type_detection():
    assert is_mutual_fund_security_type("MUTUAL_FUND")
    assert is_mutual_fund_security_type("Mutual Fund")
    assert not is_mutual_fund_security_type("STOCK")
