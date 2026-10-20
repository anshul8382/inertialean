"""Detective allocation: evidence → clear → focus uncleared / specific security."""
import pytest

from services.reconciliation.allocation_engine import run_allocation

pytestmark = pytest.mark.no_app


def test_matched_holdings_clear_reco_set():
    matched = [{"symbol": f"M{i}", "statement_qty": 10, "system_qty": 10} for i in range(5)]
    emails = [
        {
            "id": "ok",
            "sent_at": "2025-06-01",
            "subject": "Recos June 2025",
            "is_recommendation": True,
            "lines": [
                {"action": "buy", "symbol": "M0", "quantity": 10},
                {"action": "buy", "symbol": "M1", "quantity": 10},
                {"action": "buy", "symbol": "M2", "quantity": 10},
                {"action": "sell", "symbol": "M3", "quantity": 5},
            ],
        }
    ]
    out = run_allocation(
        breaks=[],
        matched_rows=matched,
        emails=emails,
        trades_by_symbol={},
        clear_min_evidences=3,
    )
    assert out["cleared_recos"]
    assert out["cleared_recos"][0]["id"] == "ok"
    assert "in sync" in (out["cleared_recos"][0].get("sync_label") or "")
    assert out["focus_recos"] == []


def test_large_mismatch_allocates_other_reco():
    matched = [{"symbol": "OK1", "statement_qty": 1, "system_qty": 1}]
    breaks = [
        {"symbol": "A", "delta": -100, "statement_qty": 0, "system_qty": 100},
        {"symbol": "B", "delta": -80, "statement_qty": 20, "system_qty": 100},
        {"symbol": "C", "delta": -50, "statement_qty": 50, "system_qty": 100},
    ]
    emails = [
        {
            "id": "cleared",
            "sent_at": "2025-01-01",
            "subject": "Old OK batch",
            "is_recommendation": True,
            "lines": [
                {"action": "buy", "symbol": "OK1", "quantity": 1},
                {"action": "buy", "symbol": "OK2", "quantity": 1},
                {"action": "buy", "symbol": "OK3", "quantity": 1},
            ],
        },
        {
            "id": "problem",
            "sent_at": "2025-11-14",
            "subject": "Nov problem sells",
            "is_recommendation": True,
            "lines": [
                {"action": "sell", "symbol": "A", "quantity": 100},
                {"action": "sell", "symbol": "B", "quantity": 80},
                {"action": "sell", "symbol": "C", "quantity": 50},
            ],
        },
    ]
    # Give cleared email enough matched evidence
    matched += [
        {"symbol": "OK2", "statement_qty": 1, "system_qty": 1},
        {"symbol": "OK3", "statement_qty": 1, "system_qty": 1},
    ]
    out = run_allocation(
        breaks=breaks,
        matched_rows=matched,
        emails=emails,
        trades_by_symbol={},
        clear_min_evidences=3,
    )
    assert any(c["id"] == "cleared" for c in out["cleared_recos"])
    assert out["focus_recos"]
    assert out["focus_recos"][0]["id"] == "problem"
    assert "A" in out["focus_recos"][0]["cover_symbols"]
    details = out["focus_recos"][0].get("symbol_details") or []
    assert details
    # stmt short vs books long on SELL reco → executed not recorded
    a_det = next(d for d in details if d["symbol"] == "A")
    assert a_det["preferred"] == "executed_not_recorded"
    assert a_det.get("explained_by_reco") is True
    assert "gap" in (a_det.get("reason") or "").lower()
    story = out.get("advisor_story") or {}
    assert "executed but not updated" in (story.get("headline") or "").lower()
    assert "Nov" in (story.get("headline") or "") or "2025-11" in str(story.get("sent_at") or "")
    assert "A" in (story.get("fixes") or [])
    hy_codes = {h["code"] for h in out["hypotheses"]}
    assert "EXECUTED_NOT_RECORDED" in hy_codes


def test_qty_gate_rejects_bajfinance_style_gap():
    """BUY 7 cannot explain gap −70 → corp action / other, not reco missed."""
    breaks = [
        {"symbol": "BAJFINANCE", "delta": -70, "statement_qty": 30, "system_qty": 100},
        {"symbol": "TCS", "delta": -50, "statement_qty": 0, "system_qty": 50},
    ]
    emails = [
        {
            "id": "nov",
            "sent_at": "2024-11-04",
            "subject": "Nov 2024 recos",
            "is_recommendation": True,
            "lines": [
                {"action": "buy", "symbol": "BAJFINANCE", "quantity": 7},
                {"action": "sell", "symbol": "TCS", "quantity": 50},
            ],
        }
    ]
    out = run_allocation(
        breaks=breaks,
        matched_rows=[],
        emails=emails,
        trades_by_symbol={},
        clear_min_evidences=99,
    )
    assert out["focus_recos"]
    details = {d["symbol"]: d for d in out["focus_recos"][0]["symbol_details"]}
    assert details["BAJFINANCE"]["preferred"] == "corp_action_or_other"
    assert details["BAJFINANCE"]["explained_by_reco"] is False
    assert details["TCS"]["explained_by_reco"] is True
    assert "BAJFINANCE" in (out["focus_recos"][0].get("leftover_symbols") or [])
    assert "TCS" in (out["focus_recos"][0].get("explained_symbols") or [])
    res_syms = {r["symbol"] for r in out["residuals"]}
    assert "BAJFINANCE" in res_syms


def test_notes_match_books_cue():
    breaks = [{"symbol": "X", "delta": -10, "statement_qty": 0, "system_qty": 10}]
    emails = [
        {
            "id": "e",
            "sent_at": "2025-08-01",
            "subject": "Aug",
            "is_recommendation": True,
            "lines": [{"action": "sell", "symbol": "X", "quantity": 10}],
        }
    ]
    out = run_allocation(
        breaks=breaks,
        matched_rows=[],
        emails=emails,
        holdings_notes_drift=[],
        holdings_notes_meta={
            "has_notes": True,
            "row_count": 20,
            "drift_count": 0,
            "sync_through": "2025-06-01",
        },
        clear_min_evidences=99,
    )
    assert out["notes_focus"]["matches_books"] is True
    assert "2025-06-01" in (out["notes_focus"].get("cue") or "")
    assert "match books" in (out["notes_focus"].get("cue") or "").lower() or "notes" in (
        out.get("action") or ""
    ).lower()


def test_specific_security_on_cleared_reco():
    matched = [{"symbol": f"M{i}", "statement_qty": 10, "system_qty": 10} for i in range(1, 4)]
    breaks = [
        {"symbol": "M0", "delta": -5, "statement_qty": 5, "system_qty": 10},
    ]
    emails = [
        {
            "id": "mostly_ok",
            "sent_at": "2025-03-01",
            "subject": "Mostly executed",
            "is_recommendation": True,
            "lines": [
                {"action": "buy", "symbol": "M0", "quantity": 10},
                {"action": "buy", "symbol": "M1", "quantity": 10},
                {"action": "buy", "symbol": "M2", "quantity": 10},
                {"action": "buy", "symbol": "M3", "quantity": 10},
            ],
        }
    ]
    out = run_allocation(
        breaks=breaks, matched_rows=matched, emails=emails, trades_by_symbol={}
    )
    # 3 matched lines → cleared; M0 still a break → specific security
    assert out["cleared_recos"]
    assert out["specific_security_issues"]
    assert out["specific_security_issues"][0]["symbol"] == "M0"


def test_sync_label_partial():
    matched = [{"symbol": "OK1", "statement_qty": 1, "system_qty": 1}]
    emails = [
        {
            "id": "partial",
            "sent_at": "2025-04-01",
            "subject": "Partial sync",
            "is_recommendation": True,
            "lines": [
                {"action": "buy", "symbol": "OK1", "quantity": 1},
                {"action": "buy", "symbol": "OK2", "quantity": 1},
                {"action": "buy", "symbol": "OK3", "quantity": 1},
                {"action": "buy", "symbol": "OK4", "quantity": 1},
                {"action": "sell", "symbol": "BAD", "quantity": 10},
            ],
        }
    ]
    matched += [
        {"symbol": "OK2", "statement_qty": 1, "system_qty": 1},
        {"symbol": "OK3", "statement_qty": 1, "system_qty": 1},
        {"symbol": "OK4", "statement_qty": 1, "system_qty": 1},
    ]
    breaks = [{"symbol": "BAD", "delta": -10, "statement_qty": 0, "system_qty": 10}]
    out = run_allocation(
        breaks=breaks, matched_rows=matched, emails=emails, clear_min_evidences=3
    )
    # 4/5 lines matched → cleared with sync label; BAD is specific-security
    assert out["cleared_recos"]
    assert out["cleared_recos"][0]["sync_label"] == "4/5 recommendations in sync"
    assert out["specific_security_issues"]
    assert out["specific_security_issues"][0]["symbol"] == "BAD"


def test_amount_gap_large_flag():
    breaks = [{"symbol": "BIG", "delta": -1000, "price": 600}]
    emails = [
        {
            "id": "e",
            "sent_at": "2025-09-01",
            "subject": "Big miss",
            "is_recommendation": True,
            "lines": [{"action": "sell", "symbol": "BIG", "quantity": 1000}],
        }
    ]
    out = run_allocation(
        breaks=breaks,
        matched_rows=[],
        emails=emails,
        trades_by_symbol={},
        clear_min_evidences=99,
        rupee_materiality=500_000,
    )
    assert out["large_amount_gap"] is True
    assert out["amount_gap_inr"] >= 500_000


def test_typo_hypothesis_when_trade_10x():
    breaks = [{"symbol": "X", "delta": -100, "statement_qty": 0, "system_qty": 100}]
    emails = [
        {
            "id": "t",
            "sent_at": "2025-08-01",
            "subject": "Typo batch",
            "is_recommendation": True,
            "lines": [{"action": "sell", "symbol": "X", "quantity": 100}],
        }
    ]
    trades = {
        "X": [
            {
                "date": "2025-08-02",
                "type": "SELL",
                "quantity": 1000,
                "price": 10,
            }
        ]
    }
    out = run_allocation(
        breaks=breaks,
        matched_rows=[],
        emails=emails,
        trades_by_symbol=trades,
        clear_min_evidences=99,
    )
    assert out["focus_recos"]
    assert any(h["code"] == "EXEC_OR_RECORD_TYPO" for h in out["hypotheses"])
