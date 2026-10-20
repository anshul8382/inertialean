"""Holdings exceed reco / duplicate vs executed-not-recorded cues."""
import pytest

from services.reconciliation.holdings_vs_reco import assess_holdings_vs_reco

pytestmark = pytest.mark.no_app


def test_holdings_exceed_only_when_fallback_enabled():
    args = dict(
        symbol="INFY",
        statement_qty=100,
        system_qty=100,
        delta=0,
        recos=[
            {"action": "buy", "quantity": 40, "sent_at": "2026-05-01", "status": "executed"},
            {"action": "buy", "quantity": 20, "sent_at": "2026-06-01", "status": "sent"},
        ],
        trades=[],
    )
    quiet = assess_holdings_vs_reco(**args)
    assert "HOLDINGS_EXCEED_RECO" not in quiet["hypotheses"]
    assert quiet["findings"] == []

    out = assess_holdings_vs_reco(**args, include_holdings_exceed=True)
    assert out["advisor_confirm"] is True
    assert "HOLDINGS_EXCEED_RECO" in out["hypotheses"]
    assert "previously held" in (out["summary"] or "").lower()


def test_other_investigations_failed_helper():
    from services.reconciliation.holdings_vs_reco import other_investigations_failed

    assert other_investigations_failed({"top_hypothesis": "TIMING_SETTLEMENT_BATCH"})
    assert other_investigations_failed({"top_hypothesis": "QTY_BREAK"})
    assert not other_investigations_failed(
        {"top_hypothesis": "FOUND_MISSING_RECOMMENDATION — Jun 2026"}
    )
    assert not other_investigations_failed(
        {"top_hypothesis": "UNKNOWN", "liquid_bees": {"matches": True}}
    )
    assert not other_investigations_failed(
        {
            "top_hypothesis": "QTY_BREAK",
            "broker_evidence": {
                "missing_from_books": [{"verdict": "EXECUTED_NOT_RECORDED"}]
            },
        }
    )


def test_delta_matches_reco_duplicate_only_when_books_already_have_trade():
    """Duplicate = gap replicates an existing booked trade."""
    out = assess_holdings_vs_reco(
        symbol="MARICO",
        statement_qty=105,
        system_qty=70,
        delta=35,
        recos=[
            {
                "action": "buy",
                "quantity": 35,
                "sent_at": "2026-05-08",
                "status": "executed",
                "executed_at": "2026-05-10",
            }
        ],
        trades=[{"type": "BUY", "quantity": 35, "date": "2026-05-10"}],
    )
    assert "POSSIBLE_DUPLICATE_CLIENT_ORDER" in out["hypotheses"]
    assert "EXECUTED_NOT_RECORDED" not in out["hypotheses"]
    assert "bought twice" in (out["summary"] or "").lower()
    assert out["advisor_confirm"] is True


def test_ather_gap_matches_reco_no_trade_is_executed_not_recorded():
    """ATHERENERG-style: holdings ahead by reco qty, zero books trades → not duplicate."""
    out = assess_holdings_vs_reco(
        symbol="ATHERENERG",
        statement_qty=30,
        system_qty=0,
        delta=30,
        recos=[
            {
                "action": "buy",
                "quantity": 30,
                "sent_at": "2026-06-16",
                "status": "sent",
            }
        ],
        trades=[],
    )
    assert out["top_hypothesis"] == "EXECUTED_NOT_RECORDED"
    assert "POSSIBLE_DUPLICATE_CLIENT_ORDER" not in out["hypotheses"]
    assert "duplicate" not in (out["summary"] or "").lower()
    assert "not in our books" in (out["summary"] or "").lower()
    assert "fill" not in (out["summary"] or "").lower()
    assert out["suspects"][0].get("next_check") == "trade_statements"
    assert out["suspects"][0].get("pipeline")


def test_reco_marked_executed_but_no_book_trade_is_not_duplicate():
    """Reco status alone is not enough for duplicate — need a books fill."""
    out = assess_holdings_vs_reco(
        symbol="HDFCBANK",
        statement_qty=50,
        system_qty=0,
        delta=50,
        recos=[
            {
                "action": "buy",
                "quantity": 50,
                "sent_at": "2026-06-01",
                "status": "executed",
                "executed_at": "2026-06-02",
            }
        ],
        trades=[],
    )
    assert out["top_hypothesis"] == "EXECUTED_NOT_RECORDED"
    assert "POSSIBLE_DUPLICATE_CLIENT_ORDER" not in out["hypotheses"]


def test_no_cue_when_holdings_within_reco():
    out = assess_holdings_vs_reco(
        symbol="TCS",
        statement_qty=10,
        system_qty=10,
        delta=0,
        recos=[{"action": "buy", "quantity": 10, "status": "executed"}],
        trades=[{"type": "BUY", "quantity": 10}],
    )
    assert out["findings"] == []
    assert out["advisor_confirm"] is False
