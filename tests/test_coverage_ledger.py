"""Coverage ledger: one |Δ| attribution, trust gate for primary cause."""
import pytest

from services.reconciliation.coverage_ledger import (
    apply_primary_cause_trust,
    build_coverage_ledger,
)

pytestmark = pytest.mark.no_app


def _breaks(syms_deltas):
    return [
        {
            "symbol": s,
            "delta": d,
            "statement_qty": 10,
            "system_qty": 10 - d,
        }
        for s, d in syms_deltas
    ]


def test_no_double_count_across_cluster_and_sell():
    breaks = _breaks([("A", -100), ("B", -100), ("C", -100)])
    # Both probes claim A,B — ledger must attribute once
    cluster = {
        "found": True,
        "coverage_abs_delta": 0.9,
        "explained_symbols": ["A", "B"],
        "emails": [{"subject": "Near", "sent_at": "2026-09-01"}],
    }
    sell = {
        "found": True,
        "coverage_abs_delta": 0.9,
        "explained_symbols": ["A", "B", "C"],
        "emails": [{"subject": "Nov", "sent_at": "2025-11-14"}],
        "message": "hist",
    }
    led = build_coverage_ledger(
        breaks=breaks, email_cluster=cluster, historical_sell_batch=sell
    )
    assert abs(led["combined_coverage_abs"] - 1.0) < 0.01
    # Near cluster has priority over hist sell for overlapping symbols
    by_sym = {a["symbol"]: a["reason_code"] for a in led["attributions"]}
    assert by_sym["A"] == "NEAR_RECO_CLUSTER"
    assert by_sym["B"] == "NEAR_RECO_CLUSTER"
    assert by_sym["C"] == "HISTORICAL_SELL_BATCH"
    assert led["trust_primary_cause"] is True
    assert led["level"] == "high"


def test_low_coverage_demotes_primary():
    breaks = _breaks([(f"S{i}", -10) for i in range(10)])
    sell = {
        "found": True,
        "coverage_abs_delta": 0.1,
        "explained_symbols": ["S0"],
        "emails": [{"subject": "Weak", "sent_at": "2025-01-01"}],
        "message": "weak",
    }
    led = build_coverage_ledger(breaks=breaks, historical_sell_batch=sell)
    assert led["level"] == "low"
    assert led["trust_primary_cause"] is False
    assert led["combined_coverage_abs"] < 0.40
    pc = apply_primary_cause_trust(
        {
            "code": "PARTIAL_DEMAT_OR_OTHER_ACCOUNT",
            "title": "Partial demat",
            "summary": "x",
            "checks": ["Pull other DP"],
        },
        led,
    )
    assert pc["demoted"] is True
    assert pc["trusted"] is False


def test_raghu_success_bar_medium_when_sell_covers_half():
    """Success bar: MEDIUM+ when hist SELL covers ≥40% of |Δ| with real subject."""
    # 1083-ish scale: big gaps partly covered by Nov sells
    breaks = _breaks(
        [
            ("AFFLE", -50),
            ("KOTAKBANK", -150),
            ("IEX", -259),
            ("BAJFINANCE", -70),
            ("HDFCBANK", -70),
            ("ETERNAL", -80),
            ("OTHER1", -100),
            ("OTHER2", -100),
            ("OTHER3", -100),
            ("OTHER4", -104),
        ]
    )
    # Cover AFFLE+KOTAK+BAJ+HDFC ≈ 340 / 1083 ≈ 0.31 — need more for medium
    sell = {
        "found": True,
        "explained_symbols": [
            "AFFLE",
            "KOTAKBANK",
            "BAJFINANCE",
            "HDFCBANK",
            "ETERNAL",
            "OTHER1",
        ],
        "emails": [
            {
                "subject": "Portfolio Investment Recos for Raghu Nov 2025",
                "sent_at": "2025-11-14",
            }
        ],
        "message": "Nov batch",
    }
    led = build_coverage_ledger(breaks=breaks, historical_sell_batch=sell)
    # 50+150+70+70+80+100 = 520 / 1083 ≈ 0.48
    assert led["combined_coverage_abs"] >= 0.40
    assert led["level"] in ("medium", "high")
    assert led["trust_primary_cause"] is True
    assert "Nov 2025" in (led["top_reasons"][0].get("subjects") or [""])[0]
    assert led["action"]
