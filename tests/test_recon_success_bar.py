"""Raghu-class success bar for holdings recon overall payloads."""
import pytest

from services.reconciliation.success_bar import evaluate_run_success_bar

pytestmark = pytest.mark.no_app


def test_low_pass_when_demoted_and_incomplete():
    overall = {
        "investigation_confidence": {
            "level": "low",
            "trust_primary_cause": False,
            "summary": "Only 20% of |Δ| attributed — investigation incomplete.",
            "action": "Find 1–2 reco emails…",
            "combined_coverage_abs": 0.2,
            "top_reasons": [],
        },
        "primary_cause": {
            "code": "PARTIAL_DEMAT_OR_OTHER_ACCOUNT",
            "title": "Partial demat",
            "demoted": True,
            "trusted": False,
        },
    }
    out = evaluate_run_success_bar(overall)
    assert out["ok"] is True
    assert out["mode"] == "low_incomplete"


def test_low_fails_if_primary_still_trusted():
    overall = {
        "investigation_confidence": {
            "level": "low",
            "trust_primary_cause": True,
            "summary": "incomplete",
            "action": "x",
        },
        "primary_cause": {"title": "Partial", "demoted": False, "trusted": True},
    }
    out = evaluate_run_success_bar(overall)
    assert out["ok"] is False
    assert "low_must_not_trust_primary_cause" in out["failures"]


def test_medium_pass_with_nov_subject():
    overall = {
        "investigation_confidence": {
            "level": "medium",
            "trust_primary_cause": True,
            "combined_coverage_abs": 0.48,
            "summary": "Partial: 48%",
            "action": "Book/reconcile Nov…",
            "top_reasons": [
                {
                    "title": "Full-archive SELL",
                    "subjects": ["Portfolio Investment Recos for Raghu Nov 2025"],
                    "sent_ats": ["2025-11-14"],
                }
            ],
        },
        "primary_cause": {
            "code": "HISTORICAL_SELL_BATCH_UNBOOKED",
            "title": "Hist sell",
            "trusted": True,
            "demoted": False,
        },
    }
    out = evaluate_run_success_bar(overall)
    assert out["ok"] is True
    assert out["mode"] == "trusted_medium"
