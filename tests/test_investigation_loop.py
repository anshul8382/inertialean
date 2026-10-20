"""Multi-pass investigation + template narrative (no app)."""
from datetime import date

import pytest

from services.reconciliation.investigation_loop import run_investigation_passes
from services.reconciliation.narrative import narrate_investigation, template_narrative

pytestmark = pytest.mark.no_app


def _system_long_breaks(n=6):
    """Partial holdings: stmt ~ half of sys → PARTIAL_DEMAT ranks high."""
    out = []
    for i in range(n):
        sys_q = 20.0 + i
        stmt_q = sys_q * 0.5
        out.append(
            {
                "symbol": f"S{i}",
                "delta": stmt_q - sys_q,
                "statement_qty": stmt_q,
                "system_qty": sys_q,
                "diagnosis": {
                    "if_reco_applied": {"explains_gap": False},
                    "trade_story": {"hypothesis": "POSSIBLE_MISSED_SELL", "recent_trades": []},
                    "emails": {"symbol_hints": []},
                },
            }
        )
    return out


def test_iterates_past_portfolio_pattern_and_ranks():
    breaks = _system_long_breaks(6)
    loop = run_investigation_passes(
        as_of=date(2026, 9, 13),
        matched_count=10,
        breaks=breaks,
        timing={
            "applied": False,
            "lag_days": 2,
            "cleared_ratio": 0.25,
            "extended_probe": {
                "shift_days": -60,
                "cleared_ratio": 0.25,
                "ratio_need": 0.6,
                "would_clear": False,
            },
        },
        email_cluster={"found": False, "message": "No cluster"},
        reco_if_executed_match=[],
    )
    names = [p["name"] for p in loop["investigation_passes"]]
    assert "gap_decomposition" in names
    assert "rank_explanations" in names
    assert loop["stopped_at_pass"] == 6
    assert loop["primary_cause"]["code"] == "PARTIAL_DEMAT_OR_OTHER_ACCOUNT"
    assert loop["ranked_explanations"]
    assert loop["ranked_explanations"][0]["score"] >= 40


def test_historical_sell_batch_surfaces_without_suppressing_peers():
    """Full-archive SELL probe ranks by coverage; does not demote partial-demat."""
    breaks = _system_long_breaks(8)
    sell_batch = {
        "found": True,
        "coverage_abs_delta": 0.46,
        "exact_count": 1,
        "explained_symbols": ["S0", "S1", "S2"],
        "message": "Best full-archive SELL reco match (2025-11-14): 46% of system-long |Δ|.",
        "emails": [{"sent_at": "2025-11-14", "subject": "Recos Nov 2025"}],
    }
    loop = run_investigation_passes(
        as_of=date(2026, 9, 13),
        matched_count=10,
        breaks=breaks,
        timing={
            "applied": False,
            "extended_probe": {
                "shift_days": -60,
                "cleared_ratio": 0.2,
                "would_clear": False,
            },
        },
        email_cluster={"found": False},
        reco_if_executed_match=[],
        historical_sell_batch=sell_batch,
    )
    names = [p["name"] for p in loop["investigation_passes"]]
    assert "historical_sell_batch" in names
    codes = [e["code"] for e in loop["ranked_explanations"]]
    assert "HISTORICAL_SELL_BATCH_UNBOOKED" in codes
    assert "PARTIAL_DEMAT_OR_OTHER_ACCOUNT" in codes
    sell_score = next(
        e["score"]
        for e in loop["ranked_explanations"]
        if e["code"] == "HISTORICAL_SELL_BATCH_UNBOOKED"
    )
    # Coverage-proportional only: 30 + 70*0.46 ≈ 62
    assert 55 <= sell_score <= 70


def test_template_narrative_includes_ranked():
    pack = {
        "primary_cause_code": "PARTIAL_DEMAT_OR_OTHER_ACCOUNT",
        "primary_cause_title": "Statement looks like a partial demat vs full books",
        "primary_cause_summary": "Median ratio 75%.",
        "primary_cause_checks": ["Pull other DP statement."],
        "ranked_explanations": [
            {
                "code": "PARTIAL_DEMAT_OR_OTHER_ACCOUNT",
                "title": "Partial demat",
                "score": 88,
                "why": "Median 75%",
                "next_checks": ["Pull other DP"],
            }
        ],
        "gap_decomposition": {
            "median_stmt_over_sys": 0.75,
            "partial_count": 20,
            "missing_count": 2,
        },
        "investigation_passes": [],
    }
    nar = template_narrative(pack)
    assert "PARTIAL" in nar["cited_cause_code"] or "Partial" in nar["prose"]
    assert "Ranked explanations" in nar["prose"] or "75%" in nar["prose"]


def test_narrate_investigation_is_template_only():
    pack = {
        "primary_cause_code": "MIXED_BREAKS",
        "primary_cause_title": "Mixed",
        "primary_cause_summary": "Mixed breaks.",
        "primary_cause_checks": ["Review break rows."],
        "investigation_passes": [],
    }
    nar = narrate_investigation(pack)
    assert nar["source"] == "template"
    assert nar["model"] is None
    assert "Mixed" in nar["prose"]
    assert nar["next_checks"]
