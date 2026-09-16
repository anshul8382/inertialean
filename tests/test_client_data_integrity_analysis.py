"""Tests for DI attention analysis (deterministic path)."""

import pytest

from services import client_data_integrity_analysis_service as mod

pytestmark = pytest.mark.no_app


def test_deterministic_prices_playbook():
    case = {
        "client_id": 26,
        "client_name": "Demo",
        "overall_status": "needs_review",
        "overall_label": "Needs review",
        "deep_links": {
            "cashflow_trade": "/clients/26/cashflow-trade-integrity",
            "firm_data_integrity": "/data-integrity/client/26",
            "data_integrity": "/clients/26/data-integrity",
        },
        "sections": [
            {
                "id": "cashflow_trade",
                "title": "Cashflow ↔ trades",
                "status": "matched",
                "signal_ids": ["G8"],
                "summary": "ok",
                "items": [],
            },
            {
                "id": "prices",
                "title": "Price accuracy",
                "status": "needs_review",
                "signal_ids": ["P1"],
                "summary": "40 findings",
                "detail_url": "/hub/system/price-accuracy",
                "items": [
                    {
                        "fact_id": "price:MISSING:RELIANCE",
                        "title": "MISSING · RELIANCE — 12 finding(s) · e.g. 2018-09-13",
                        "kind": "MISSING",
                    }
                ],
            },
        ],
    }
    ctx = mod._compact_context(case)
    assert len(ctx["attention_sections"]) == 1
    out = mod._deterministic_attention(case, ctx)
    assert out["source"] == "deterministic"
    assert out["attention"]
    row = out["attention"][0]
    assert row["section_id"] == "prices"
    assert "Hub" in " ".join(row["how_to_resolve"]) or "price" in " ".join(row["how_to_resolve"]).lower()
    assert row["open_url"] == "/hub/system/price-accuracy"
    assert "RELIANCE" in (row.get("finding_summaries") or [""])[0]


def test_llm_validation_allowlists_sections():
    case = {
        "client_id": 1,
        "deep_links": {},
        "sections": [],
    }
    fallback = {"headline": "fb", "attention": []}
    raw = {
        "headline": "Focus on prices",
        "attention": [
            {
                "section_id": "prices",
                "priority": 1,
                "title": "Fix RELIANCE gaps",
                "why_it_matters": "Missing closes",
                "how_to_resolve": ["Open Hub price accuracy", "Fill missing dates"],
                "cited_fact_ids": ["price:MISSING:RELIANCE", "fake:id"],
            },
            {
                "section_id": "invented",
                "how_to_resolve": ["noop"],
            },
        ],
    }
    validated = mod._validate_llm_attention(
        raw,
        allow_sections={"prices"},
        allow_facts={"price:MISSING:RELIANCE"},
        case=case,
        fallback=fallback,
    )
    assert validated is not None
    assert validated["source"] == "llm"
    assert len(validated["attention"]) == 1
    assert validated["attention"][0]["cited_fact_ids"] == ["price:MISSING:RELIANCE"]
