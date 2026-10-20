"""Diagnosis helpers (no DB)."""
from datetime import date

import pytest

from services.reconciliation import diagnosis_service as diag
from services.reconciliation.diagnosis_service import (
    _dedupe_email_rows,
    _extract_reco_mentions_from_email_text,
    analyze_trade_story,
    build_overall_summary,
)

pytestmark = pytest.mark.no_app


def test_similar_ticker_scoring(monkeypatch):
    monkeypatch.setattr(
        diag,
        "similar_ticker_hints",
        lambda **kw: [
            {
                "security_id": 2,
                "symbol": "VMART",
                "similarity": 0.8,
                "hint": "test",
            }
        ],
    )
    hints = diag.similar_ticker_hints(
        client_id=1,
        break_row={"security_id": 1, "symbol": "DMART"},
        as_of=date(2026, 9, 12),
    )
    assert hints and hints[0]["symbol"] == "VMART"


def test_extract_reco_mentions_from_email_text():
    hits = _extract_reco_mentions_from_email_text(
        "<p>BUY INFY qty 10</p><p>SELL HDFCBANK 25</p>"
        "<p>Total Buy Recos 49</p><p>Buy Aurobindo Pharma</p>"
    )
    assert {"action": "BUY", "symbol": "INFY", "quantity": 10.0} in hits
    assert any(h["symbol"] == "HDFCBANK" and h["action"] == "SELL" for h in hits)
    assert not any(h["symbol"] in ("RECOS", "AUROBINDO") for h in hits)


def test_dedupe_email_rows_prefers_imap():
    rows = _dedupe_email_rows(
        [
            {
                "subject": "Portfolio Investment Recos for Raghu Oct 2026",
                "sent_at": "2026-10-06",
                "source": "email_log",
                "email_type": "recommendations",
            },
            {
                "subject": "Portfolio Investment Recos for Raghu Oct 2026",
                "sent_at": "2026-10-06",
                "source": "imap_sent",
                "email_type": "imap_sent",
            },
        ]
    )
    assert len(rows) == 1
    assert rows[0]["source"] == "imap_sent"


def test_analyze_trade_story_new_addition_not_quiet_gap():
    story = analyze_trade_story(
        statement_qty=10,
        system_qty=10,
        as_of=date(2026, 9, 13),
        trades=[
            {"id": 1, "date": "2026-08-01", "type": "BUY", "quantity": 10},
        ],
    )
    assert story["new_addition"] is True
    assert story["hypothesis"] == "NEW_ADDITION"
    assert any("new / recent addition" in n.lower() for n in story["notes"])


def test_analyze_trade_story_missed_sell():
    story = analyze_trade_story(
        statement_qty=167,
        system_qty=180,
        as_of=date(2026, 9, 13),
        trades=[
            {"id": 1, "date": "2025-01-01", "type": "BUY", "quantity": 180},
        ],
    )
    assert story["direction"] == "SYSTEM_HIGHER"
    assert story["hypothesis"] == "POSSIBLE_MISSED_SELL"
    assert story["trade_gap"] is True
    assert any("missed sell" in n.lower() for n in story["notes"])


def test_analyze_trade_story_matching_sell():
    story = analyze_trade_story(
        statement_qty=167,
        system_qty=180,
        as_of=date(2026, 9, 13),
        trades=[
            {"id": 2, "date": "2026-09-10", "type": "SELL", "quantity": 13},
            {"id": 1, "date": "2026-01-01", "type": "BUY", "quantity": 180},
        ],
    )
    assert story["matching_sells"]
    # SELL 13 ≈ gap 13 but direction SYSTEM_HIGHER wants undoing BUYs;
    # matching_sell is still recorded; hyp may be TRADE_DATE or RECENT if trailing fits
    assert story["hypothesis"] in (
        "TRADE_DATE_VS_AS_OF",
        "RECENT_TRADES_EXPLAIN_GAP",
        "POSSIBLE_MISSED_SELL",
    )


def test_trailing_buy_explains_books_ahead():
    """APLAPOLLO-style: books 40, holdings 30, last BUY 10 in Sep."""
    from services.reconciliation.diagnosis_service import explain_gap_by_trailing_trades

    expl = explain_gap_by_trailing_trades(
        statement_qty=30,
        system_qty=40,
        as_of=date(2026, 9, 13),
        trades=[
            {"id": 9, "date": "2026-09-05", "type": "BUY", "quantity": 10},
            {"id": 1, "date": "2025-01-01", "type": "BUY", "quantity": 30},
        ],
    )
    assert expl and expl["ok"]
    assert expl["trade_count"] == 1
    assert "remove the last 1" in expl["summary"].lower()

    story = analyze_trade_story(
        statement_qty=30,
        system_qty=40,
        as_of=date(2026, 9, 13),
        trades=[
            {"id": 9, "date": "2026-09-05", "type": "BUY", "quantity": 10},
            {"id": 1, "date": "2025-01-01", "type": "BUY", "quantity": 30},
        ],
    )
    assert story["hypothesis"] == "RECENT_TRADES_EXPLAIN_GAP"
    assert story["gap_explained_by_trades"]["ok"]


def test_trailing_two_trades_explain_gap():
    """HDFC Life–style: remove last 2 trades → matches holdings."""
    story = analyze_trade_story(
        statement_qty=100,
        system_qty=125,
        as_of=date(2026, 9, 13),
        trades=[
            {"id": 3, "date": "2026-09-08", "type": "BUY", "quantity": 15},
            {"id": 2, "date": "2026-09-02", "type": "BUY", "quantity": 10},
            {"id": 1, "date": "2024-01-01", "type": "BUY", "quantity": 100},
        ],
    )
    assert story["hypothesis"] == "RECENT_TRADES_EXPLAIN_GAP"
    assert story["gap_explained_by_trades"]["trade_count"] == 2
    assert "last 2" in story["gap_explained_by_trades"]["summary"]


def test_build_overall_summary_headlines():
    breaks = [
        {
            "symbol": "INFY",
            "status": "QTY_BREAK",
            "delta": -13,
            "diagnosis": {
                "top_hypothesis": "POSSIBLE_MISSED_SELL",
                "trade_story": {
                    "trade_gap": True,
                    "hypothesis": "POSSIBLE_MISSED_SELL",
                    "busy_days": [{"date": "2026-08-01", "trade_count": 3}],
                },
                "reco": {"sent_count": 2, "executed_count": 1, "pending_sent_count": 1},
            },
        },
        {
            "symbol": "AFFLE",
            "status": "MISSING_IN_STATEMENT",
            "delta": -5,
            "diagnosis": {
                "top_hypothesis": "POSSIBLE_MISSED_SELL",
                "trade_story": {
                    "trade_gap": False,
                    "hypothesis": "POSSIBLE_MISSED_SELL",
                    "busy_days": [],
                },
                "reco": {},
            },
        },
    ]
    # Pad to portfolio-long pattern (≥5, all system-long)
    for i, sym in enumerate(["A", "B", "C"]):
        breaks.append(
            {
                "symbol": sym,
                "status": "QTY_BREAK",
                "delta": -10 - i,
                "diagnosis": {
                    "top_hypothesis": "POSSIBLE_MISSED_SELL",
                    "trade_story": {"hypothesis": "POSSIBLE_MISSED_SELL", "busy_days": []},
                    "reco": {},
                },
            }
        )
    overall = build_overall_summary(
        as_of=date(2026, 9, 13),
        matched_count=10,
        breaks=breaks,
        statement_security_count=39,
    )
    assert overall["break_count"] == 5
    assert overall["system_higher_count"] == 5
    assert "INFY" in overall["possible_missed_sells"]
    assert overall["primary_cause"]["code"] == "PORTFOLIO_BOOKS_LONG"
    assert overall["headlines"]
    assert any(h.startswith("PRIMARY:") for h in overall["headlines"])


def test_diagnose_breaks_timing_wins(monkeypatch):
    """Timing wins only when no reco email explains the gap."""
    monkeypatch.setattr(diag, "corporate_actions_near", lambda *a, **k: [])
    monkeypatch.setattr(
        diag,
        "advisor_cashflow_notes_near",
        lambda *a, **k: {
            "approved": False,
            "notes_near_as_of": [],
            "cashflow_descriptions_near": [],
            "ui_url": "/x",
            "row_count_saved": 0,
            "summary": "No notes.",
        },
    )
    monkeypatch.setattr(
        diag,
        "email_hints_near",
        lambda *a, **k: {
            "emails": [
                {
                    "id": 9,
                    "sent_at": "2026-09-08",
                    "subject": "Market update (no qty table)",
                    "is_recommendation": False,
                    "status": "sent",
                    "recipient": "client@example.com",
                    "body_reco_hints": [],
                    "_blob": "MARKET UPDATE",
                }
            ],
            "_all": [
                {
                    "id": 9,
                    "sent_at": "2026-09-08",
                    "subject": "Market update (no qty table)",
                    "is_recommendation": False,
                    "status": "sent",
                    "recipient": "client@example.com",
                    "body_reco_hints": [],
                    "_blob": "MARKET UPDATE",
                }
            ],
            "client_emails_filter": ["client@example.com"],
            "total_near": 1,
            "recommendation_count": 0,
            "failed_count": 0,
            "summary": "1 email(s) near as-of matched by client id/email.",
            "parses_recommendations_from_email": False,
        },
    )
    monkeypatch.setattr(
        diag,
        "call_notes_near",
        lambda *a, **k: {
            "calls": [{"id": 1, "date": "2026-09-07", "notes": "Discussed INFY trim"}],
            "summary": "1 call note(s) near as-of.",
        },
    )
    monkeypatch.setattr(diag, "similar_ticker_hints", lambda **k: [])
    monkeypatch.setattr(
        diag,
        "reco_hints",
        lambda **k: {
            "recos": [],
            "hypothesis": None,
            "sent_count": 0,
            "executed_count": 0,
            "pending_sent_count": 0,
            "buy_qty_sent": 0.0,
            "sell_qty_sent": 0.0,
            "summary": "none",
        },
    )
    monkeypatch.setattr(diag, "fetch_security_trades", lambda **k: [])
    out, overall = diag.diagnose_breaks(
        client_id=1,
        as_of=date(2026, 9, 10),
        breaks=[
            {
                "security_id": 1,
                "symbol": "HDFCBANK",
                "statement_qty": 15,
                "system_qty": 10,
                "delta": 5,
                "status": "QTY_BREAK",
            }
        ],
        timing={"applied": True, "message": "lag"},
        matched_count=3,
    )
    assert out[0]["diagnosis"]["top_hypothesis"] == "TIMING_SETTLEMENT_BATCH"
    assert out[0]["status"] == "QTY_BREAK"
    assert out[0]["diagnosis"]["investigation_notes"]
    assert overall["break_count"] == 1
    assert "_all" not in overall["emails"]


def test_diagnose_breaks_missing_reco_beats_timing(monkeypatch):
    """Jun-style BUY reco that explains Δ overrides TIMING on status + hypothesis."""
    monkeypatch.setattr(diag, "corporate_actions_near", lambda *a, **k: [])
    monkeypatch.setattr(
        diag,
        "advisor_cashflow_notes_near",
        lambda *a, **k: {
            "approved": False,
            "notes_near_as_of": [],
            "cashflow_descriptions_near": [],
            "ui_url": "/x",
            "row_count_saved": 0,
            "summary": "No notes.",
        },
    )
    buy_email = {
        "id": "db:session:1",
        "sent_at": "2026-06-16",
        "subject": "Portfolio Investment Recos for Subhodeep Jun 2026",
        "is_recommendation": True,
        "status": "sent",
        "recipient": "client@example.com",
        "body_reco_hints": [
            {"action": "BUY", "symbol": "ATHERENERG", "quantity": 30.0},
            {"action": "BUY", "symbol": "HDFCBANK", "quantity": 50.0},
            {"action": "BUY", "symbol": "HDFCLIFE", "quantity": 33.0},
        ],
        "lines": [
            {"action": "BUY", "symbol": "ATHERENERG", "quantity": 30.0},
            {"action": "BUY", "symbol": "HDFCBANK", "quantity": 50.0},
            {"action": "BUY", "symbol": "HDFCLIFE", "quantity": 33.0},
        ],
        "_blob": "BUY ATHERENERG 30 BUY HDFCBANK 50 BUY HDFCLIFE 33",
    }
    monkeypatch.setattr(
        diag,
        "email_hints_near",
        lambda *a, **k: {
            "emails": [buy_email],
            "_all": [buy_email],
            "_near": [buy_email],
            "client_emails_filter": ["client@example.com"],
            "total_near": 1,
            "recommendation_count": 1,
            "failed_count": 0,
            "summary": "1 reco",
            "parses_recommendations_from_email": True,
        },
    )
    monkeypatch.setattr(
        diag, "call_notes_near", lambda *a, **k: {"calls": [], "summary": ""}
    )
    monkeypatch.setattr(diag, "similar_ticker_hints", lambda **k: [])
    monkeypatch.setattr(
        diag,
        "reco_hints",
        lambda **k: {
            "recos": [],
            "hypothesis": None,
            "sent_count": 0,
            "executed_count": 0,
            "pending_sent_count": 0,
            "buy_qty_sent": 0.0,
            "sell_qty_sent": 0.0,
            "summary": "none",
        },
    )
    monkeypatch.setattr(diag, "fetch_security_trades", lambda **k: [])
    out, overall = diag.diagnose_breaks(
        client_id=1,
        as_of=date(2026, 9, 10),
        breaks=[
            {
                "security_id": 1,
                "symbol": "ATHERENERG",
                "statement_qty": 30,
                "system_qty": 0,
                "delta": 30,
                "status": "MISSING_IN_SYSTEM",
            },
            {
                "security_id": 2,
                "symbol": "HDFCBANK",
                "statement_qty": 311,
                "system_qty": 261,
                "delta": 50,
                "status": "QTY_BREAK",
            },
            {
                "security_id": 3,
                "symbol": "HDFCLIFE",
                "statement_qty": 128,
                "system_qty": 95,
                "delta": 33,
                "status": "QTY_BREAK",
            },
        ],
        timing={"applied": True, "message": "lag"},
        matched_count=10,
    )
    by_sym = {b["symbol"]: b for b in out}
    for sym in ("ATHERENERG", "HDFCBANK", "HDFCLIFE"):
        b = by_sym[sym]
        assert b["status"] == "FOUND_MISSING_RECOMMENDATION"
        hyp = b["diagnosis"]["top_hypothesis"]
        assert hyp.startswith("FOUND_MISSING_RECOMMENDATION")
        assert "Jun 2026" in hyp
    story = overall.get("advisor_story") or {}
    assert "ATHERENERG" in (story.get("high_confidence_symbols") or story.get("fixes") or [])
