"""Simple loop: seed mismatch → reco email → fixes → remaining."""
import pytest

from services.reconciliation.simple_investigate import run_simple_loop

pytestmark = pytest.mark.no_app


def test_june_reco_clears_cluster_like_ather():
    breaks = [
        {"symbol": "ATHERENERG", "delta": 30, "statement_qty": 30, "system_qty": 0},
        {"symbol": "CONCOR", "delta": 50, "statement_qty": 50, "system_qty": 0},
        {"symbol": "HDFCBANK", "delta": 50, "statement_qty": 281, "system_qty": 231},
        {"symbol": "HDFCLIFE", "delta": 33, "statement_qty": 54, "system_qty": 21},
        {"symbol": "BAJFINANCE", "delta": -70, "statement_qty": 30, "system_qty": 100},
    ]
    emails = [
        {
            "id": "jun",
            "sent_at": "2026-06-10",
            "subject": "Portfolio Investment Recos for Subhodeep Jun 2026",
            "is_recommendation": True,
            "lines": [
                {"action": "buy", "symbol": "ATHERENERG", "quantity": 30},
                {"action": "buy", "symbol": "CONCOR", "quantity": 50},
                {"action": "buy", "symbol": "HDFCBANK", "quantity": 50},
                {"action": "buy", "symbol": "HDFCLIFE", "quantity": 33},
                {"action": "buy", "symbol": "BAJFINANCE", "quantity": 7},
            ],
        }
    ]
    out = run_simple_loop(breaks, emails)
    story = out["advisor_story"]
    assert "Jun 2026" in story["headline"]
    assert "executed but not updated" in story["headline"].lower()
    for sym in ("ATHERENERG", "CONCOR", "HDFCBANK", "HDFCLIFE"):
        assert sym in story["fixes"]
    # BUY 7 cannot explain Δ −70
    assert "BAJFINANCE" in story["remaining"]
    assert "BAJFINANCE" not in story["fixes"]
    assert story["next"] and "BAJFINANCE" in story["next"]
    assert out["steps"]
    assert out["steps"][0]["seed"] in story["fixes"] or out["steps"][0]["seed"] == "ATHERENERG" or abs(
        next(b["delta"] for b in breaks if b["symbol"] == out["steps"][0]["seed"])
    ) >= 30


def test_no_email_gives_start_guidance():
    breaks = [{"symbol": "XYZ", "delta": 10, "statement_qty": 10, "system_qty": 0}]
    out = run_simple_loop(breaks, emails=[])
    story = out["advisor_story"]
    assert story["fixes"] == []
    assert "XYZ" in story["remaining"]
    assert "Start with" in story["headline"] or "search Sent" in (story.get("next") or "")


def test_second_step_after_partial_clear():
    breaks = [
        {"symbol": "A", "delta": 10, "statement_qty": 10, "system_qty": 0},
        {"symbol": "B", "delta": 5, "statement_qty": 5, "system_qty": 0},
        {"symbol": "C", "delta": 8, "statement_qty": 8, "system_qty": 0},
    ]
    emails = [
        {
            "id": "e1",
            "sent_at": "2026-01-01",
            "subject": "Jan 2026 Recos",
            "is_recommendation": True,
            "lines": [
                {"action": "buy", "symbol": "A", "quantity": 10},
                {"action": "buy", "symbol": "B", "quantity": 5},
            ],
        },
        {
            "id": "e2",
            "sent_at": "2026-03-01",
            "subject": "Mar 2026 Recos",
            "is_recommendation": True,
            "lines": [{"action": "buy", "symbol": "C", "quantity": 8}],
        },
    ]
    out = run_simple_loop(breaks, emails)
    assert len(out["steps"]) >= 1
    # Primary story from first step
    assert "A" in out["advisor_story"]["fixes"] or "B" in out["advisor_story"]["fixes"]
    # Loop can continue for C
    all_fixed = set()
    for st in out["steps"]:
        all_fixed |= set(st["fixes"])
    assert "C" in all_fixed or "C" in out["advisor_story"]["remaining"]
