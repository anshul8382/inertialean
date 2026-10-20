"""Per-remaining-mismatch collapsible checks."""
import pytest

from services.reconciliation.remaining_mismatch_checks import build_remaining_note

pytestmark = pytest.mark.no_app


def test_gap_5_reco_without_trade_is_missing_trade():
    note = build_remaining_note(
        symbol="INFY",
        delta=5,
        statement_qty=150,
        system_qty=145,
        email_lines=[{"action": "buy", "quantity": 5, "sent_at": "2026-06-01", "subject": "Jun"}],
        recos=[{"action": "buy", "quantity": 5, "sent_at": "2026-06-01", "status": "sent"}],
        trades=[],
    )
    assert note["gap"] == 5
    text = " ".join(note["potential_issues"] + note["checks"]).lower()
    assert "missing trade" in text or "add the trade" in text
    assert any(s.get("quantity") == 5 for s in note["suspects"])
    assert "BUY 5" in " ".join(note["checks"]).upper() or "buy 5" in text


def test_gap_5_trade_without_reco_is_incorrect_entry():
    note = build_remaining_note(
        symbol="TCS",
        delta=-5,
        statement_qty=95,
        system_qty=100,
        email_lines=[],
        recos=[],
        trades=[{"type": "SELL", "quantity": 5, "date": "2026-06-02"}],
    )
    text = " ".join(note["potential_issues"] + note["checks"]).lower()
    assert "incorrect" in text or "without" in text
    assert any(s.get("kind") == "trade_qty_matches_gap" for s in note["suspects"])


def test_what_if_buy_adjustment():
    note = build_remaining_note(
        symbol="X",
        delta=10,
        statement_qty=10,
        system_qty=0,
        email_lines=[],
        recos=[],
        trades=[],
    )
    assert any("What-if" in c and "BUY 10" in c for c in note["checks"])
