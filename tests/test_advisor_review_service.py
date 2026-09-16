"""Unit tests for advisor review metrics helpers."""

from datetime import date

import pytest

from services.advisor_review_service import (
    _growth_and_trend,
    _month_bounds,
    _shift_month,
    format_inr_compact,
)

pytestmark = pytest.mark.no_app


def test_format_inr_compact_scales():
    assert format_inr_compact(5000) == "₹5,000"
    assert "Lakh" in format_inr_compact(250_000)
    assert "Cr" in format_inr_compact(15_000_000)


def test_shift_month_and_bounds():
    assert _shift_month(date(2026, 3, 15), -1) == date(2026, 2, 1)
    assert _shift_month(date(2026, 1, 10), -1) == date(2025, 12, 1)
    start, end = _month_bounds(date(2026, 2, 5))
    assert start == date(2026, 2, 1)
    assert end == date(2026, 2, 28)


def test_growth_up_down_flat():
    up = _growth_and_trend(120.0, 100.0, [])
    assert up["trend"] == "up"
    assert up["growth_pct"] == 20.0

    down = _growth_and_trend(80.0, 100.0, [])
    assert down["trend"] == "down"
    assert down["growth_pct"] == -20.0

    flat = _growth_and_trend(0.0, 0.0, [])
    assert flat["trend"] == "flat"

    new = _growth_and_trend(50.0, 0.0, [])
    assert new["trend"] == "up"
    assert new["growth_pct"] is None


def test_assignment_overview_scopes_to_advisor(monkeypatch):
    """Advisor-scoped overview returns only that advisor's clients."""
    from types import SimpleNamespace

    from services import advisor_review_service as svc

    class FakeClient:
        def __init__(self, cid, name, advisor_id, advisor=None):
            self.id = cid
            self.name = name
            self.advisor_id = advisor_id
            self.advisor = advisor

    adv_a = SimpleNamespace(id=10, username="alice", email="a@x")
    adv_b = SimpleNamespace(id=20, username="bob", email="b@x")
    by = {
        10: [FakeClient(1, "C1", 10, adv_a), FakeClient(2, "C2", 10, adv_a)],
        20: [FakeClient(3, "C3", 20, adv_b)],
        None: [FakeClient(99, "Orphan", None, None)],
    }
    monkeypatch.setattr(svc, "_active_clients_by_advisor", lambda: by)

    scoped = svc.get_advisor_assignment_overview(advisor_id=10)
    assert scoped["scoped_to_self"] is True
    assert scoped["advisor_count"] == 1
    assert scoped["assigned_client_count"] == 2
    assert scoped["unassigned_count"] == 0
    assert scoped["groups"][0]["advisor_id"] == 10

    firm = svc.get_advisor_assignment_overview()
    assert firm["scoped_to_self"] is False
    assert firm["advisor_count"] == 2
    assert firm["unassigned_count"] == 1
    assert firm["assigned_client_count"] == 3
