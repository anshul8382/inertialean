"""Reco trade-window + resolved/pending counts."""
import pytest

from services.reconciliation.execution_evidence import (
    enrich_story_counts,
    reco_trade_window,
    summarize_trade_hits,
)

pytestmark = pytest.mark.no_app


def test_jun_window_is_jun_jul():
    start, end, label = reco_trade_window("2026-06-15")
    assert str(start) == "2026-06-01"
    assert str(end) == "2026-07-31"
    assert "Jun" in label and "Jul" in label


def test_summarize_all_missing_supports_not_booked():
    hit = summarize_trade_hits(
        fix_details=[
            {"symbol": "ATHERENERG", "action": "buy", "quantity": 30},
            {"symbol": "CONCOR", "action": "buy", "quantity": 50},
        ],
        trades_by_symbol={"ATHERENERG": [], "CONCOR": []},
    )
    assert hit["all_missing"] is True
    assert len(hit["trades_missing"]) == 2


def test_summarize_matching_trade():
    hit = summarize_trade_hits(
        fix_details=[{"symbol": "INFY", "action": "buy", "quantity": 5}],
        trades_by_symbol={
            "INFY": [{"type": "buy", "quantity": 5, "date": "2026-06-20"}]
        },
    )
    assert hit["all_found"] is True
    assert len(hit["trades_matching"]) == 1


def test_enrich_story_counts():
    story = enrich_story_counts(
        {
            "headline": "Looks like the Jun 2026 recommendation was executed but not updated.",
            "subject": "Portfolio Investment Recos for Subhodeep Jun 2026",
            "sent_at": "2026-06-10",
            "fixes": ["ATHERENERG", "CONCOR", "HDFCBANK"],
            "remaining": ["BAJFINANCE", "X"],
        }
    )
    assert story["resolved_count"] == 3
    assert story["pending_count"] == 2
    assert story["gap_total"] == 5
    assert "3 of 5" in story["resolved_summary"]
    assert "Jun 2026" in story["resolved_summary"]
    assert "ATHERENERG" in story["high_confidence_symbols"]


def test_found_missing_recommendation_labels_override_timing():
    from services.reconciliation.execution_evidence import (
        apply_found_missing_recommendation_labels,
    )

    story = enrich_story_counts(
        {
            "subject": "Portfolio Investment Recos for Subhodeep Jun 2026",
            "sent_at": "2026-06-16",
            "fixes": ["ATHERENERG", "HDFCBANK", "HDFCLIFE"],
            "remaining": ["OTHER"],
        }
    )
    breaks = [
        {
            "symbol": "ATHERENERG",
            "status": "MISSING_IN_SYSTEM",
            "diagnosis": {"top_hypothesis": "TIMING_SETTLEMENT_BATCH"},
        },
        {
            "symbol": "HDFCBANK",
            "status": "QTY_BREAK",
            "diagnosis": {"top_hypothesis": "TIMING_SETTLEMENT_BATCH"},
        },
        {
            "symbol": "OTHER",
            "status": "QTY_BREAK",
            "diagnosis": {"top_hypothesis": "TIMING_SETTLEMENT_BATCH"},
        },
    ]
    apply_found_missing_recommendation_labels(breaks, story)
    assert breaks[0]["status"] == "FOUND_MISSING_RECOMMENDATION"
    assert "FOUND_MISSING_RECOMMENDATION" in breaks[0]["diagnosis"]["top_hypothesis"]
    assert "Jun 2026" in breaks[0]["diagnosis"]["top_hypothesis"]
    assert breaks[0]["diagnosis"]["match_status"] == "MISSING_IN_SYSTEM"
    assert breaks[0]["diagnosis"]["prior_hypothesis"] == "TIMING_SETTLEMENT_BATCH"
    assert breaks[1]["status"] == "FOUND_MISSING_RECOMMENDATION"
    # Untouched residual keeps timing label
    assert breaks[2]["status"] == "QTY_BREAK"
    assert breaks[2]["diagnosis"]["top_hypothesis"] == "TIMING_SETTLEMENT_BATCH"
