"""Batch email cluster + 3-way delta explainers (no app)."""
import pytest

from services.reconciliation.reco_delta_match import (
    find_best_historical_sell_batch,
    find_explaining_email_cluster,
    if_reco_applied_explains_gap,
    line_explains_delta,
    three_way_explain,
)

pytestmark = pytest.mark.no_app


def test_line_explains_buy_and_sell_delta():
    assert line_explains_delta("buy", 4, 4.0) == 1.0
    assert line_explains_delta("sell", 13, -13.0) == 1.0
    assert line_explains_delta("buy", 4, -13.0) == 0.0


def test_cluster_one_email_explains_most_breaks():
    breaks = [
        {"symbol": "AUROPHARMA", "delta": 4},
        {"symbol": "NAUKRI", "delta": 5},
        {"symbol": "PIIND", "delta": 3},
        {"symbol": "HAVELLS", "delta": 7},
        {"symbol": "INFY", "delta": -13},  # unrelated
    ]
    emails = [
        {
            "id": "e1",
            "sent_at": "2026-10-06",
            "subject": "Portfolio Investment Recos for Raghu Oct 2026",
            "lines": [
                {"action": "buy", "symbol": "AUROPHARMA", "quantity": 4},
                {"action": "buy", "symbol": "NAUKRI", "quantity": 5},
                {"action": "buy", "symbol": "PIIND", "quantity": 3},
                {"action": "buy", "symbol": "HAVELLS", "quantity": 7},
            ],
        }
    ]
    out = find_explaining_email_cluster(emails, breaks, min_coverage=0.45)
    assert out["found"] is True
    assert "AUROPHARMA" in out["explained_symbols"]
    assert out["message"]


def test_three_way_email_sell_no_trade():
    out = three_way_explain(
        symbol="INFY",
        delta=-13,
        statement_qty=167,
        system_qty=180,
        email_hints=[
            {
                "action": "sell",
                "symbol": "INFY",
                "quantity": 13,
                "email_sent_at": "2026-09-01",
            }
        ],
        recos=[{"id": 9, "action": "sell", "quantity": 13, "status": "sent"}],
        trades=[{"type": "BUY", "quantity": 180, "date": "2025-01-01"}],
    )
    assert out["hypothesis"] in (
        "EMAIL_SELL_EXPLAINS_DELTA",
        "EMAIL_SELL_NO_TRADE",
        "RECO_OPEN_EXPLAINS_DELTA",
    )
    assert any("3-way" in n for n in out["notes"])


def test_three_way_buy_email_does_not_hijack_system_long():
    out = three_way_explain(
        symbol="INFY",
        delta=-13,
        statement_qty=167,
        system_qty=180,
        email_hints=[
            {"action": "buy", "symbol": "INFY", "quantity": 3, "email_sent_at": "2025-12-16"}
        ],
        recos=[],
        trades=[{"type": "BUY", "quantity": 180, "date": "2025-01-01"}],
    )
    assert out["hypothesis"] != "EMAIL_BUY_NO_TRADE"
    assert any("does not explain system-long" in n for n in out["notes"])


def test_historical_sell_batch_covers_system_long_gaps():
    breaks = [
        {"symbol": "AFFLE", "delta": -50, "statement_qty": 0, "system_qty": 50},
        {"symbol": "KOTAKBANK", "delta": -150, "statement_qty": 470, "system_qty": 620},
        {"symbol": "IEX", "delta": -259, "statement_qty": 350, "system_qty": 609},
        {"symbol": "BAJFINANCE", "delta": -70, "statement_qty": 150, "system_qty": 220},
    ]
    emails = [
        {
            "id": "nov",
            "sent_at": "2025-11-14",
            "subject": "Portfolio Investment Recos for Raghu Nov 2025",
            "lines": [
                {"action": "sell", "symbol": "NSE:AFFLE", "quantity": 50},
                {"action": "sell", "symbol": "KOTAKBANK", "quantity": 125},
                {"action": "sell", "symbol": "BAJFINANCE", "quantity": 400},
            ],
        }
    ]
    out = find_best_historical_sell_batch(emails, breaks)
    assert "AFFLE" in out["explained_symbols"]
    assert out["exact_count"] >= 1
    assert out["coverage_abs_delta"] > 0
    assert "2025-11-14" in (out["message"] or "")
    # With only partial cover of these gaps, found may be false — facts still returned
    assert out["message"]


def test_if_reco_applied_sell_closes_gap():
    out = if_reco_applied_explains_gap(
        symbol="AFFLE",
        statement_qty=0,
        system_qty=10,
        email_hints=[{"action": "sell", "symbol": "AFFLE", "quantity": 10}],
        recos=[],
    )
    assert out["explains_gap"] is True
    assert out["hypothesis"] == "IF_RECO_EXECUTED_MATCHES"
    assert out["sell_qty"] == 10
    assert "match" in (out["message"] or "").lower()


def test_if_reco_applied_sell_does_not_explain():
    out = if_reco_applied_explains_gap(
        symbol="HAVELLS",
        statement_qty=90,
        system_qty=100,
        email_hints=[{"action": "sell", "symbol": "HAVELLS", "quantity": 3}],
        recos=[],
    )
    assert out["explains_gap"] is False
    assert out["sell_qty"] == 3
    assert "does not fully explain" in (out["message"] or "").lower()
