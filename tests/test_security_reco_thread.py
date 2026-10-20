"""Thread B: matched/mismatched inventory + security- vs reco-wise mode."""
import pytest

from services.reconciliation.security_reco_thread import build_security_reco_thread

pytestmark = pytest.mark.no_app


def test_few_mismatches_use_security_wise_mode():
    matched = [
        {"symbol": "INFY", "security_id": 1, "statement_qty": 10, "system_qty": 10},
        {"symbol": "TCS", "security_id": 2, "statement_qty": 5, "system_qty": 5},
    ]
    breaks = [
        {
            "symbol": "AFFLE",
            "security_id": 3,
            "statement_qty": 0,
            "system_qty": 50,
            "delta": -50,
            "status": "MISSING_IN_STATEMENT",
            "diagnosis": {
                "if_reco_applied": {"explains_gap": True, "message": "SELL 50 closes gap"},
                "three_way": {},
                "trade_story": {},
                "emails": {"symbol_hints": [{"action": "SELL", "symbol": "AFFLE", "quantity": 50}]},
                "reco": {"recos": [{}]},
                "top_hypothesis": "MISSED_RECO_EXECUTION",
            },
        },
        {
            "symbol": "IEX",
            "security_id": 4,
            "statement_qty": 100,
            "system_qty": 200,
            "delta": -100,
            "status": "QTY_BREAK",
            "diagnosis": {
                "if_reco_applied": {"explains_gap": False},
                "three_way": {},
                "trade_story": {"hypothesis": "POSSIBLE_MISSED_SELL"},
                "emails": {"symbol_hints": []},
                "reco": {"recos": []},
                "top_hypothesis": "POSSIBLE_MISSED_SELL",
            },
        },
    ]
    out = build_security_reco_thread(
        matched_rows=matched, breaks=breaks, emails=[], matched_count=2
    )
    assert out["mode"] == "security_wise"
    assert out["matched_count"] == 2
    assert out["mismatch_count"] == 2
    assert [m["symbol"] for m in out["matched"]] == ["INFY", "TCS"]
    assert out["explainable_count"] == 1
    assert out["unexplained_count"] == 1
    assert any("AFFLE" in f for f in out["findings"])


def test_many_mismatches_highlight_concentrated_reco():
    matched = [{"symbol": f"M{i}", "security_id": i} for i in range(5)]
    breaks = []
    for i, sym in enumerate(["A", "B", "C", "D", "E"]):
        breaks.append(
            {
                "symbol": sym,
                "security_id": 100 + i,
                "statement_qty": 10,
                "system_qty": 20,
                "delta": -10,
                "status": "QTY_BREAK",
                "diagnosis": {
                    "if_reco_applied": {"explains_gap": False},
                    "three_way": {},
                    "trade_story": {},
                    "emails": {"symbol_hints": []},
                    "reco": {},
                },
            }
        )
    emails = [
        {
            "id": "nov",
            "sent_at": "2025-11-14",
            "subject": "Portfolio Investment Recos Nov 2025",
            "is_recommendation": True,
            "lines": [
                {"action": "sell", "symbol": "A", "quantity": 10},
                {"action": "sell", "symbol": "B", "quantity": 10},
                {"action": "sell", "symbol": "C", "quantity": 10},
                {"action": "sell", "symbol": "D", "quantity": 5},
            ],
        },
        {
            "id": "other",
            "sent_at": "2025-01-01",
            "subject": "Unrelated",
            "is_recommendation": True,
            "lines": [{"action": "buy", "symbol": "ZZZ", "quantity": 1}],
        },
    ]
    out = build_security_reco_thread(
        matched_rows=matched, breaks=breaks, emails=emails, matched_count=5
    )
    assert out["mode"] == "reco_wise"
    assert out["highlighted_recos"]
    top = out["highlighted_recos"][0]
    assert top["id"] == "nov"
    assert top["break_name_count"] >= 3
    assert "A" in top["issue_symbols"]
    assert out["issue_recos"]
