"""Unit tests for client data integrity case roll-up (mocked deps)."""

import pytest

from services import client_data_integrity_case_service as mod

pytestmark = pytest.mark.no_app


def test_g8_status_mapping():
    assert mod._g8_to_section_status("matched") == "matched"
    assert mod._g8_to_section_status("ignore_opening_book") == "matched"
    assert mod._g8_to_section_status("mismatch_review") == "needs_review"
    assert mod._g8_to_section_status("mismatch_material") == "not_matched"
    assert mod._g8_to_section_status(None) == "unknown"


def test_worse_wins():
    assert mod._worse("matched", "needs_review") == "needs_review"
    assert mod._worse("needs_review", "not_matched") == "not_matched"
    assert mod._worse("matched", "matched") == "matched"


def test_build_case_overall_from_sections(monkeypatch):
    class FakeClient:
        name = "Test Client"

    class FakeClientModel:
        class query:
            @staticmethod
            def get(_id):
                return FakeClient()

    monkeypatch.setattr(
        mod,
        "_section_cashflow_trade",
        lambda client_id, live, detail: (
            {
                "id": "cashflow_trade",
                "title": "Cashflow ↔ trades",
                "status": "matched",
                "signal_ids": ["G8"],
                "open_count": 0,
                "summary": "ok",
                "items": [],
            },
            {"status": "matched", "difference": 0},
        ),
    )
    monkeypatch.setattr(
        mod,
        "_sections_from_open_issues",
        lambda client_id: [
            {
                "id": "duplicates",
                "title": "Duplicate trades",
                "status": "not_matched",
                "signal_ids": ["G1"],
                "open_count": 2,
                "summary": "2 open",
                "items": [{"fact_id": "issue:1", "title": "dup"}],
            }
        ],
    )
    monkeypatch.setattr(mod, "_section_prices", lambda client_id: None)

    import models

    monkeypatch.setattr(models, "Client", FakeClientModel)

    case = mod.build_case(42, include_g8_detail=True, live_g8=False)
    assert case["overall_status"] == "not_matched"
    assert case["overall_label"] == "Not matched"
    assert case["client_name"] == "Test Client"
    assert case["module_id"] == "client_data_integrity"
    assert case["suggest_only"] is True
    assert any(s["id"] == "duplicates" for s in case["sections"])


def test_build_client_summary_chips(monkeypatch):
    monkeypatch.setattr(
        mod,
        "build_case",
        lambda client_id, include_g8_detail=False, live_g8=True, include_prices=True: {
            "overall_status": "needs_review",
            "sections": [
                {
                    "id": "cashflow_trade",
                    "title": "Cashflow ↔ trades",
                    "status": "matched",
                    "open_count": 0,
                },
                {
                    "id": "dates",
                    "title": "Date integrity",
                    "status": "needs_review",
                    "open_count": 1,
                },
            ],
            "as_of": "t",
        },
    )
    summary = mod.build_client_summary(7, live_g8=False)
    assert summary["overall_label"] == "Needs review"
    assert summary["url"].endswith("/clients/7/data-integrity")
    assert summary["open_section_count"] == 1
    assert any(c["id"] == "dates" for c in summary["section_chips"])


def test_refilter_drops_stale_nightly_price_summaries():
    analysis = {
        "from_nightly": True,
        "attention": [
            {
                "section_id": "prices",
                "title": "Price accuracy: INFY, HDFCLIFE",
                "finding_summaries": [
                    "MISSING · INFY — 25 finding(s) · e.g. 2017-07-09, 2019-05-01, 2019-05-05"
                ],
            }
        ],
        "headline": "old",
    }
    case = {
        "client_id": 1,
        "overall_label": "Matched",
        "sections": [
            {
                "id": "prices",
                "status": "matched",
                "items": [],
            }
        ],
    }
    out = mod.refilter_analysis_after_price_refresh(analysis, case)
    assert not any(i.get("section_id") == "prices" for i in out["attention"])


def test_strip_noisy_price_section_clears_weekend_groups():
    sec = {
        "id": "prices",
        "status": "needs_review",
        "items": [
            {
                "kind": "MISSING",
                "symbol": "INFY",
                "title": "MISSING · INFY — 25 finding(s) · e.g. 2017-07-09, 2019-05-05",
            }
        ],
    }
    out = mod._strip_noisy_price_section(sec)
    assert out["status"] == "matched"
    assert out["items"] == []
