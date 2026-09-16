"""
Unit tests for client health observation pack + nightly ranking (no DB / no LLM).
"""

import pytest

from services.client_health_observations import build_pack, make_observation
from services.client_health_nightly_service import (
    build_consolidated_digests,
    interpret_client_observations,
    observation_to_card_item,
)
from services.client_health_priority_service import assign_priority_band, rank_violations

pytestmark = pytest.mark.no_app


def test_build_pack_groups_by_client():
    obs = [
        make_observation(
            client_id=1, signal_id="A2", source="wf", signal="RECOS_delay",
            severity="critical", title="Funds ready", assignee_user_id=9,
        ),
        make_observation(
            client_id=1, signal_id="G1", source="issue", signal="duplicate_transaction",
            severity="critical", title="Dup", assignee_user_id=9,
        ),
        make_observation(
            client_id=2, signal_id="A1", source="wf", signal="FUNDS_delay",
            severity="warning", title="Funds pending", assignee_user_id=8,
        ),
    ]
    pack = build_pack(obs, run_id="test")
    assert pack["client_count"] == 2
    assert pack["observation_count"] == 3


def test_a2_and_b1_are_p0():
    assert assign_priority_band({"signal_id": "A2", "severity": "warning", "title": "x"}) == "P0"
    assert assign_priority_band({"signal_id": "B1", "severity": "warning", "title": "x"}) == "P0"
    assert assign_priority_band({"signal_id": "A1", "severity": "warning", "title": "x"}) == "P1"


def test_interpret_fail_closed_without_llm(monkeypatch):
    monkeypatch.setenv("CLIENT_HEALTH_USE_LLM", "0")
    obs = [
        make_observation(
            client_id=1, signal_id="B1", source="wf", signal="recommendation_not_sent",
            severity="critical", title="Funds ready; recs not sent",
            facts={"funds_ready": True, "business_hours": 30, "investment_amount": 100000},
        )
    ]
    result = interpret_client_observations(obs, use_llm=False)
    assert result["copy_source"] == "deterministic"
    assert len(result["violations"]) <= 3
    assert result["recommendations"]
    assert "funds" in result["violations"][0]["improvement_action"].lower() or "recommendation" in result["violations"][0]["improvement_action"].lower()


def test_consolidated_digest_groups_by_assignee():
    rows = [
        {
            "client_id": 1,
            "assignee_user_id": 5,
            "violations": [{"title": "A", "improvement_action": "Do A", "signal_id": "B1"}],
        },
        {
            "client_id": 2,
            "assignee_user_id": 5,
            "violations": [{"title": "B", "improvement_action": "Do B", "signal_id": "C1"}],
        },
        {
            "client_id": 3,
            "assignee_user_id": 7,
            "violations": [{"title": "C", "improvement_action": "Do C", "signal_id": "A1"}],
        },
    ]
    digests = build_consolidated_digests(rows)
    assert digests["digest_count"] == 2
    by_uid = {d["recipient_user_id"]: d["line_count"] for d in digests["digests"]}
    assert by_uid[5] == 2
    assert by_uid[7] == 1


def test_observation_to_card_item_uses_business_hours():
    obs = make_observation(
        client_id=1, signal_id="A2", source="wf", signal="RECOS_delay",
        severity="warning", title="x", facts={"business_hours": 36},
    )
    item = observation_to_card_item(obs)
    assert item["age_hours"] == 36
    ranked = rank_violations([item])
    assert ranked and ranked[0]["priority_band"] == "P0"
