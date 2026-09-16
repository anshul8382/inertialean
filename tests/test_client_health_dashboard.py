"""Tests for Client Health dashboard grouping (no DB required for pack path)."""

from datetime import datetime, timedelta

import pytest

from services.client_health_dashboard_service import (
    CLOSE_WITHIN_DAYS,
    DATA_SIGNAL_IDS,
    group_data_price_by_client,
)
from services.client_health_nightly_service import build_consolidated_digests
from services.client_health_observations import make_observation

pytestmark = pytest.mark.no_app


def test_data_signal_ids_include_g_and_p1():
    assert "G1" in DATA_SIGNAL_IDS
    assert "P1" in DATA_SIGNAL_IDS
    assert CLOSE_WITHIN_DAYS == 14


def test_group_from_pack_marks_overdue(monkeypatch):
    old = (datetime.utcnow() - timedelta(days=20)).isoformat() + "Z"
    fresh = datetime.utcnow().isoformat() + "Z"
    import services.client_health_dashboard_service as dash

    monkeypatch.setattr(
        dash,
        "_from_observations_pack",
        lambda ids: (
            [
                {
                    "client_id": 1,
                    "client_name": "Alpha",
                    "assignee_user_id": 9,
                    "signal_id": "G1",
                    "title": "Dup",
                    "detected_at": old,
                    "issue_id": 10,
                },
                {
                    "client_id": 1,
                    "client_name": "Alpha",
                    "assignee_user_id": 9,
                    "signal_id": "P1",
                    "title": "Spike",
                    "detected_at": fresh,
                    "issue_id": None,
                },
            ],
            {"source": "client_health_pack"},
        ),
    )

    rows = group_data_price_by_client(accessible_client_ids={1})
    assert len(rows) == 1
    assert rows[0]["issue_count"] == 2
    assert rows[0]["overdue_count"] == 1
    assert "G1" in rows[0]["signals"] and "P1" in rows[0]["signals"]


def test_pack_drops_resolved_issue_backed_observations(monkeypatch):
    """Resolved duplicates must not keep counting on the by-client header list."""
    import services.client_health_dashboard_service as dash

    pack = {
        "as_of": "2026-08-06T17:31:44.740066Z",
        "clients": [
            {
                "client_id": 137,
                "observations": [
                    {
                        "id": "issue:2559",
                        "signal_id": "G1",
                        "title": "PAYTM dup",
                        "detected_at": "2026-08-02T04:00:19",
                        "facts": {"issue_id": 2559},
                    },
                    {
                        "id": "price:spikes:c137:r20",
                        "signal_id": "P1",
                        "title": "Price spikes",
                        "detected_at": "2026-08-06T17:31:44",
                        "facts": {"kind": "SPIKE_ROLLUP"},
                    },
                ],
            }
        ],
    }

    monkeypatch.setattr(
        "services.client_health_observations.load_latest_observations",
        lambda: pack,
    )
    monkeypatch.setattr(dash, "_open_issue_id_set", lambda ids: set())

    items, meta = dash._from_observations_pack({137})
    assert meta["filtered_count"] == 1
    assert len(items) == 1
    assert items[0]["signal_id"] == "P1"
    assert items[0]["issue_id"] is None

    rows = group_data_price_by_client(accessible_client_ids={137})
    assert len(rows) == 1
    assert rows[0]["issue_count"] == 1
    assert rows[0]["signals"] == ["P1"]


def test_digests_include_data_price_section(monkeypatch):
    monkeypatch.setattr(
        "services.client_health_nightly_service.build_manager_data_digest_lines",
        lambda **kwargs: [
            {
                "client_id": 1,
                "client_name": "Alpha",
                "issue_count": 2,
                "overdue_count": 1,
                "oldest_age_days": 20,
                "signals": "G1,P1",
                "action": "Close",
            }
        ],
    )
    rows = [
        {
            "client_id": 1,
            "assignee_user_id": 9,
            "violations": [
                {"title": "X", "improvement_action": "Do Y", "signal_id": "G1", "priority_band": "P0"}
            ],
        }
    ]
    digests = build_consolidated_digests(rows)
    assert digests["digest_count"] == 1
    assert digests["data_price_digest"]["line_count"] == 1
    assert digests["data_price_digest"]["lines"][0]["client_id"] == 1
