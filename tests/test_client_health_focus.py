"""
Unit tests for Client Health focus: priority ranking + output wrapper final call.

No LLM / network — interpretation is mocked; wrapper validation is the focus.
"""

import pytest

from services.client_health_priority_service import (
    assign_priority_band,
    deterministic_recommendations,
    rank_violations,
)
from services.client_health_phrase_service import (
    _validate_llm_payload,
    phrase_health_copy,
)

pytestmark = pytest.mark.no_app


def _v(**kwargs):
    base = {
        "alert_id": 1,
        "type": "workflow_sla",
        "subtype": "FUNDS_delay",
        "severity": "warning",
        "title": "FUNDS SLA breach",
        "description": "",
        "age_hours": 10,
        "domain": "investment_ops",
        "impact": "medium",
        "priority": "MEDIUM",
        "improvement_action": "",
    }
    base.update(kwargs)
    return base


def test_drops_info_and_unreadable_pdf():
    assert assign_priority_band(_v(severity="info")) is None
    assert (
        assign_priority_band(
            _v(severity="warning", title="PDF unreadable", description="agreement_pdf_unreadable")
        )
        is None
    )


def test_critical_and_agreement_missing_are_p0():
    assert assign_priority_band(_v(severity="critical")) == "P0"
    assert (
        assign_priority_band(
            _v(severity="warning", type="agent_orchestrated", title="agreement_missing")
        )
        == "P0"
    )


def test_rank_caps_at_three_and_orders_p0_first():
    items = [
        _v(alert_id=1, severity="warning", type="billing", title="Invoice late", age_hours=5),
        _v(alert_id=2, severity="critical", title="Duplicate transaction", age_hours=2),
        _v(alert_id=3, severity="warning", title="agreement_name_mismatch", age_hours=100),
        _v(alert_id=4, severity="warning", type="workflow_sla", title="EXEC delay", age_hours=50),
    ]
    ranked = rank_violations(items)
    assert len(ranked) <= 3
    assert ranked[0]["alert_id"] == 2  # P0 critical first
    assert ranked[0]["priority_band"] == "P0"


def test_deterministic_recommendations_focused():
    ranked = rank_violations(
        [_v(alert_id=9, severity="critical", title="No agreement", description="agreement_missing")]
    )
    recs = deterministic_recommendations(ranked)
    assert len(recs) <= 3
    assert "process optimization" not in " ".join(recs).lower()
    assert recs != []


def test_output_wrapper_rejects_invented_alert_ids():
    ranked = [_v(alert_id=10, severity="critical", title="Real issue")]
    proposed = {
        "violations": [
            {"alert_id": 10, "title": "Clear title", "improvement_action": "Do the right thing now."},
            {"alert_id": 999, "title": "Invented", "improvement_action": "Ignore me"},
        ],
        "recommendations": ["Fix the real issue today.", "task assignment create ops"],
    }
    out = _validate_llm_payload(proposed, ranked)
    assert out is not None
    assert len(out["violations"]) == 1
    assert out["violations"][0]["alert_id"] == 10
    assert out["violations"][0]["copy_source"] == "llm"
    # Task-assignment language stripped from recommendations
    assert all("task assignment" not in r.lower() for r in out["recommendations"])


def test_phrase_health_copy_fail_closed_without_llm(monkeypatch):
    monkeypatch.setenv("CLIENT_HEALTH_USE_LLM", "0")
    result = phrase_health_copy(
        [_v(alert_id=5, severity="critical", title="Negative holding", description="negative_holding")]
    )
    assert result["copy_source"] == "deterministic"
    assert len(result["violations"]) == 1
    assert len(result["recommendations"]) <= 3
