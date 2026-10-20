"""1–2 reasons covering |Δ| via coverage ledger → investigation confidence."""
import pytest

from services.reconciliation.investigation_confidence import (
    assess_investigation_confidence,
)

pytestmark = pytest.mark.no_app


def _breaks(n=10, delta=-10):
    return [
        {
            "symbol": f"S{i}",
            "delta": delta,
            "statement_qty": 10,
            "system_qty": 20,
        }
        for i in range(n)
    ]


def test_high_when_one_reco_covers_most_delta():
    breaks = _breaks(10)
    out = assess_investigation_confidence(
        breaks=breaks,
        historical_sell_batch={
            "found": True,
            "coverage_abs_delta": 0.78,
            "coverage_count": 0.7,
            "message": "Nov covers 78%",
            "emails": [{"subject": "Nov 2025", "sent_at": "2025-11-14"}],
            "explained_symbols": [f"S{i}" for i in range(8)],
        },
        matched_count=5,
    )
    assert out["level"] == "high"
    assert out["process_ok"] is True
    assert out["trust_primary_cause"] is True
    assert out["combined_coverage_abs"] >= 0.70
    assert out["top_reasons"][0]["depth"] == "full"


def test_low_when_several_breaks_but_no_clustered_reason():
    breaks = _breaks(12)
    out = assess_investigation_confidence(
        breaks=breaks,
        email_cluster={"found": False, "coverage_abs_delta": 0.1, "explained_symbols": ["S0"]},
        historical_sell_batch={
            "coverage_abs_delta": 0.15,
            "message": "weak",
            "explained_symbols": ["S1"],
            "emails": [{"subject": "x", "sent_at": "2025-01-01"}],
        },
        security_reco_thread={
            "mode": "reco_wise",
            "highlighted_recos": [
                {
                    "subject": "Scattered",
                    "sent_at": "2025-01-01",
                    "coverage_abs_delta": 0.12,
                    "break_name_count": 2,
                    "issue_name_count": 2,
                    "touched_symbols": ["S0", "S1"],
                    "issue_symbols": ["S0", "S1"],
                }
            ],
        },
        matched_count=8,
    )
    assert out["level"] == "low"
    assert out["trust_primary_cause"] is False
    assert "incomplete" in out["summary"].lower()
    assert out["action"]


def test_medium_partial_cover():
    breaks = _breaks(8)
    out = assess_investigation_confidence(
        breaks=breaks,
        historical_sell_batch={
            "found": True,
            "coverage_abs_delta": 0.48,
            "coverage_count": 0.4,
            "message": "partial",
            "emails": [{"subject": "Nov", "sent_at": "2025-11-14"}],
            "explained_symbols": ["S0", "S1", "S2", "S3"],
        },
    )
    assert out["level"] == "medium"
    assert out["trust_primary_cause"] is True
    assert out["top_reasons"][0]["depth"] == "partial"


def test_security_wise_two_breaks_explainable():
    breaks = _breaks(2)
    out = assess_investigation_confidence(
        breaks=breaks,
        security_reco_thread={
            "mode": "security_wise",
            "explainable_count": 2,
            "per_security": [
                {"symbol": "S0", "explainable": True},
                {"symbol": "S1", "explainable": True},
            ],
        },
    )
    assert out["level"] == "high"
    assert out["trust_primary_cause"] is True
    assert out["process_ok"] is True
