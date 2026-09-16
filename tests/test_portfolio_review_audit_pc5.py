"""PC5 corporate-action artefact check — uses CorporateAction + adjusted prices."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.portfolio_review_audit_engine import (
    collect_large_loss_symbols,
    evaluate_large_loss_with_corporate_actions,
    _format_ca_action_summary,
)

pytestmark = pytest.mark.no_app


def test_collect_large_loss_symbols_from_raw_holdings():
    raw = (
        "Holdings (SYMBOL  START_PRICE  END_PRICE  RETURN%):\n"
        "KOTAKBANK  1526.00  378.00  -75.20%\n"
        "INFY  1465.00  1077.00  -26.45%\n"
    )
    losers = collect_large_loss_symbols(raw)
    names = {x["name"] for x in losers}
    assert "KOTAKBANK" in names
    assert "INFY" not in names
    kotak = next(x for x in losers if x["name"] == "KOTAKBANK")
    assert kotak["ret"] == pytest.approx(-75.20)


def test_collect_large_loss_from_period_analysis():
    pa = {
        "worst_performers": {
            "worst_performers": [
                {"symbol": "BAJFINANCE", "price_percent_change": -53.42},
                {"symbol": "PIDILITIND", "price_percent_change": -25.0},
            ]
        }
    }
    losers = collect_large_loss_symbols("", pa)
    assert {x["name"] for x in losers} == {"BAJFINANCE"}


def test_format_ca_split_summary():
    action = SimpleNamespace(
        action_type="SPLIT",
        ratio=5.0,
        action_date=date(2026, 1, 14),
    )
    assert "SPLIT 5:1" in _format_ca_action_summary(action)


@patch("services.portfolio_review_audit_engine.compute_adjusted_period_return")
@patch("services.portfolio_review_audit_engine.lookup_corporate_actions_for_symbol")
def test_pc5_ca_with_mild_adjusted_return_is_amber_artefact(mock_lookup, mock_adj):
    mock_lookup.return_value = (
        SimpleNamespace(id=10, symbol="KOTAKBANK"),
        [
            SimpleNamespace(
                action_type="SPLIT",
                ratio=5.0,
                action_date=date(2026, 1, 14),
            )
        ],
    )
    mock_adj.return_value = {
        "start_price": 305.2,
        "end_price": 378.0,
        "price_change": 72.8,
        "price_percent_change": 23.85,
    }

    out = evaluate_large_loss_with_corporate_actions(
        "KOTAKBANK",
        -75.2,
        date(2025, 4, 9),
        date(2026, 7, 15),
    )
    assert out["verdict"] == "amber"
    assert out["has_ca"] is True
    assert "Corporate action found" in out["line"]
    assert "artefact" in out["line"].lower()
    assert "23.85%" in out["line"]


@patch("services.portfolio_review_audit_engine.compute_adjusted_period_return")
@patch("services.portfolio_review_audit_engine.lookup_corporate_actions_for_symbol")
def test_pc5_no_ca_still_severe_is_red(mock_lookup, mock_adj):
    mock_lookup.return_value = (SimpleNamespace(id=11, symbol="NAUKRI"), [])
    mock_adj.return_value = {
        "start_price": 1889.0,
        "end_price": 1187.0,
        "price_change": -702.0,
        "price_percent_change": -37.17,
    }
    # reported ≤ -40 but we force evaluation path without CA; use -45 reported
    # and adj still severe (-45)
    mock_adj.return_value["price_percent_change"] = -45.0

    out = evaluate_large_loss_with_corporate_actions(
        "NAUKRI",
        -45.0,
        date(2025, 4, 9),
        date(2026, 7, 15),
    )
    assert out["verdict"] == "red"
    assert out["has_ca"] is False
    assert "No corporate action" in out["line"]


@patch("services.portfolio_review_audit_engine.compute_adjusted_period_return")
@patch("services.portfolio_review_audit_engine.lookup_corporate_actions_for_symbol")
def test_pc5_demerger_ca_flags_combined_return(mock_lookup, mock_adj):
    mock_lookup.return_value = (
        SimpleNamespace(id=12, symbol="PARENT"),
        [
            SimpleNamespace(
                action_type="DEMERGER",
                ratio=0.1,
                action_date=date(2025, 6, 1),
            )
        ],
    )
    mock_adj.return_value = {
        "start_price": 100.0,
        "end_price": 50.0,
        "price_change": -50.0,
        "price_percent_change": -50.0,
    }

    out = evaluate_large_loss_with_corporate_actions(
        "PARENT",
        -50.0,
        date(2025, 1, 1),
        date(2025, 12, 31),
    )
    assert out["verdict"] == "amber"
    assert "Demerger/merger" in out["line"]
    assert "combined return" in out["line"].lower()


@patch("services.portfolio_review_audit_engine.compute_adjusted_period_return")
@patch("services.portfolio_review_audit_engine.lookup_corporate_actions_for_symbol")
def test_pc5_restates_reported_prices_when_priceservice_misses_ca(mock_lookup, mock_adj):
    """When PriceService returns unadjusted -75%, CA restatement of table prices wins."""
    actions = [
        SimpleNamespace(
            action_type="SPLIT",
            ratio=5.0,
            action_date=date(2026, 1, 14),
        )
    ]
    mock_lookup.return_value = (
        SimpleNamespace(id=10, symbol="KOTAKBANK"),
        actions,
    )
    # PriceService failed to apply CA (still looks like raw -75%).
    mock_adj.return_value = {
        "start_price": 1526.0,
        "end_price": 378.0,
        "price_change": -1148.0,
        "price_percent_change": -75.2,
    }

    out = evaluate_large_loss_with_corporate_actions(
        "KOTAKBANK",
        -75.2,
        date(2025, 4, 9),
        date(2026, 7, 15),
        reported_start_price=1526.0,
        reported_end_price=378.0,
    )
    assert out["verdict"] == "amber"
    assert out["adjusted_ret"] is not None
    # 1526/5 = 305.2 → 378 ≈ +23.9%
    assert out["adjusted_ret"] == pytest.approx(23.85, abs=0.1)
    assert "artefact" in out["line"].lower()


def test_restate_reported_prices_hdfc_bonus():
    from services.corporate_action_price_restatement import (
        restate_reported_prices_with_actions,
    )

    actions = [
        SimpleNamespace(
            action_type="BONUS",
            ratio=1.0,
            action_date=date(2025, 8, 26),
        )
    ]
    out = restate_reported_prices_with_actions(
        1543.0,
        815.0,
        actions,
        date(2025, 4, 9),
        date(2026, 7, 15),
    )
    assert out is not None
    assert out["start_price"] == pytest.approx(771.5)
    assert out["price_percent_change"] == pytest.approx(5.64, abs=0.1)
