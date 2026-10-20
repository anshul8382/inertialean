"""Holdings exceed reco / possible duplicate client order cues."""
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
    # Default: do not surface exceed (other investigations not confirmed failed)
    quiet = assess_holdings_vs_reco(**args)
    assert "HOLDINGS_EXCEED_RECO" not in quiet["hypotheses"]
    assert quiet["findings"] == []

    # Last resort only
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


def test_delta_matches_reco_possible_duplicate_when_already_booked():
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
    assert "duplicate" in (out["summary"] or "").lower()
    assert out["advisor_confirm"] is True


def test_delta_matches_reco_mentions_duplicate_as_alternate():
    out = assess_holdings_vs_reco(
        symbol="HDFCBANK",
        statement_qty=50,
        system_qty=0,
        delta=50,
        recos=[{"action": "buy", "quantity": 50, "sent_at": "2026-06-01", "status": "sent"}],
        trades=[],
    )
    assert any("duplicate" in f.lower() for f in out["findings"])
    assert out["advisor_confirm"] is True


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
