"""Crisp advisor story: reco executed not booked → fixes list → next."""
import pytest

from services.reconciliation.allocation_engine import build_advisor_story

pytestmark = pytest.mark.no_app


def test_june_reco_story():
    fr = {
        "sent_at": "2026-06-15",
        "subject": "Portfolio Investment Recos for Subhodeep Jun 2026",
        "explained_symbols": ["ATHERENERG", "CONCOR", "HDFCBANK", "HDFCLIFE"],
        "symbol_details": [
            {
                "symbol": s,
                "explained_by_reco": True,
                "preferred": "executed_not_recorded",
            }
            for s in ("ATHERENERG", "CONCOR", "HDFCBANK", "HDFCLIFE")
        ],
    }
    story = build_advisor_story(
        focus_recos=[fr],
        residuals=[
            {"symbol": "ATHERENERG"},
            {"symbol": "CONCOR"},
            {"symbol": "BAJFINANCE"},
        ],
    )
    assert "Jun 2026" in story["headline"]
    assert "executed but not updated" in story["headline"].lower()
    assert "ATHERENERG" in story["fixes"]
    assert "BAJFINANCE" in story["remaining"]
    assert "ATHERENERG" not in story["remaining"]
    assert story["next"] and "Next" in story["next"]
