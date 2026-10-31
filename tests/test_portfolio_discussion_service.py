"""Unit tests for Weekly Portfolio Discussion scoring, snooze, and helpers."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import random

from services.portfolio_discussion_service import (
    ALLOWED_EMAIL,
    XIRR_FLOOR_PCT,
    allocate_queue_slots,
    aging_sort_key,
    concern_score,
    days_since_checked,
    is_concern,
    is_snoozed,
    parse_tasks_from_form,
    pick_aging_client_ids,
    pick_random_client_ids,
    user_may_view_portfolio_discussion,
)

pytestmark = pytest.mark.no_app


def test_user_may_view_gate():
    assert user_may_view_portfolio_discussion(
        SimpleNamespace(is_authenticated=True, email=ALLOWED_EMAIL)
    )
    assert user_may_view_portfolio_discussion(
        SimpleNamespace(is_authenticated=True, email="Anshul@Equities4Wealth.com")
    )
    assert not user_may_view_portfolio_discussion(
        SimpleNamespace(is_authenticated=True, email="other@equities4wealth.com")
    )
    assert not user_may_view_portfolio_discussion(
        SimpleNamespace(is_authenticated=False, email=ALLOWED_EMAIL)
    )


def test_concern_score_absolute_and_relative():
    assert concern_score(5.0, 0.0) == pytest.approx(3.0)
    assert concern_score(10.0, 4.5) == pytest.approx(4.5)
    assert concern_score(4.0, 2.0) == pytest.approx(4.0)
    assert concern_score(6.0, 5.0) == pytest.approx(5.0)
    assert concern_score(12.0, -1.0) == pytest.approx(0.0)
    assert XIRR_FLOOR_PCT == 8.0


def test_is_concern_flags():
    assert is_concern(7.9, 0.0)
    assert is_concern(10.0, 0.01)
    assert is_concern(5.0, 3.0)
    assert not is_concern(8.0, 0.0)
    assert not is_concern(12.0, -0.5)


def test_is_snoozed_until_hide_until():
    today = date(2026, 10, 31)
    assert is_snoozed(date(2026, 10, 31), today)
    assert is_snoozed(date(2026, 11, 30), today)
    assert not is_snoozed(date(2026, 10, 30), today)
    assert not is_snoozed(None, today)


def test_parse_tasks_from_form():
    form = MagicMock()
    form.getlist.side_effect = lambda key: {
        "task_name": ["Call client", "", "Follow up"],
        "task_assigned_to": ["3", "0", "5"],
        "task_deadline": ["2026-11-15", "", "2026-12-01"],
    }[key]
    tasks = parse_tasks_from_form(form)
    assert len(tasks) == 3
    assert tasks[0]["name"] == "Call client"
    assert tasks[0]["assigned_to"] == "3"
    assert tasks[2]["deadline"] == "2026-12-01"


def test_create_discussion_tasks_skips_empty_names(monkeypatch):
    import extensions
    import models
    import services.portfolio_discussion_service as mod

    # Ensure models attributes are bound before we stub extensions.db
    assert models.OpsTask is not None

    added = []

    class FakeSession:
        def add(self, obj):
            added.append(obj)

        def commit(self):
            raise AssertionError("commit should not run when commit=False")

    class FakeOpsTask:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    monkeypatch.setattr(models, "OpsTask", FakeOpsTask)
    monkeypatch.setattr(extensions, "db", SimpleNamespace(session=FakeSession()))

    created = mod.create_discussion_tasks(
        client_id=10,
        created_by=1,
        tasks=[
            {"name": "Do thing", "assigned_to": "2", "deadline": "2026-11-10"},
            {"name": "  ", "assigned_to": "0"},
            {"name": "Second", "assigned_to": None, "deadline": None},
        ],
        commit=False,
    )
    assert len(created) == 2
    assert created[0].name == "Do thing"
    assert created[0].assigned_to == 2
    assert created[0].client_id == 10
    assert created[0].deadline == datetime(2026, 11, 10)
    assert created[1].name == "Second"
    assert created[1].assigned_to is None
    assert isinstance(created[1].deadline, datetime)
    assert len(added) == 2


def test_save_discussion_note_tasks_and_review(monkeypatch):
    import extensions
    import models
    import services.portfolio_discussion_service as mod

    assert models.Client is not None

    client = SimpleNamespace(id=42, background_notes="old")
    user = SimpleNamespace(id=7, username="anshul", email=ALLOWED_EMAIL)
    commits = {"n": 0}

    class FakeSession:
        def commit(self):
            commits["n"] += 1

    class FakeClientQuery:
        @staticmethod
        def get(cid):
            assert cid == 42
            return client

    monkeypatch.setattr(models, "Client", SimpleNamespace(query=FakeClientQuery))
    monkeypatch.setattr(extensions, "db", SimpleNamespace(session=FakeSession()))

    notes = []

    def fake_append(existing, new_note_text, author, when=None):
        notes.append((existing, new_note_text, author))
        return f"{existing}\n{new_note_text}"

    monkeypatch.setattr(
        "services.client_notes_service.append_background_notes_note",
        fake_append,
    )
    monkeypatch.setattr(
        "services.client_notes_service.note_author_name",
        lambda u: "anshul",
    )

    def fake_create(cid, created_by, tasks, commit=False):
        assert cid == 42
        assert created_by == 7
        assert tasks == [{"name": "Email client"}]
        return [SimpleNamespace(id=99)]

    reviewed = []

    def fake_mark(cid, user_id, as_of=None, note_preview=None, commit=True):
        reviewed.append(
            {
                "cid": cid,
                "user_id": user_id,
                "as_of": as_of,
                "note_preview": note_preview,
                "commit": commit,
            }
        )
        return SimpleNamespace(hide_until=as_of + timedelta(days=30))

    monkeypatch.setattr(mod, "create_discussion_tasks", fake_create)
    monkeypatch.setattr(mod, "mark_reviewed", fake_mark)

    result = mod.save_discussion(
        42,
        user,
        note_text="Rebalance equity sleeve",
        tasks=[{"name": "Email client"}],
        mark_complete=True,
        as_of=date(2026, 10, 31),
        commit=True,
    )

    assert result["note_saved"] is True
    assert result["tasks_created"] == 1
    assert result["marked_reviewed"] is True
    assert result["hide_until"] == date(2026, 11, 30)
    assert notes[0][1].startswith("Portfolio discussion:")
    assert "Rebalance equity sleeve" in notes[0][1]
    assert reviewed[0]["commit"] is False
    assert commits["n"] == 1


def test_allocate_queue_slots_fifty():
    slots = allocate_queue_slots(50)
    assert slots["concern"] == 30
    assert slots["aging"] == 15
    assert slots["random"] == 5
    assert sum(slots.values()) == 50


def test_aging_helpers_never_then_oldest():
    today = date(2026, 10, 31)
    assert days_since_checked(None, today) is None
    assert days_since_checked(datetime(2026, 10, 1), today) == 30
    assert aging_sort_key(None) < aging_sort_key(datetime(2026, 1, 1))
    assert aging_sort_key(datetime(2025, 1, 1)) < aging_sort_key(datetime(2026, 1, 1))

    picked = pick_aging_client_ids(
        [10, 20, 30],
        {
            10: datetime(2026, 9, 1),
            20: None,
            30: datetime(2025, 1, 1),
        },
        n=2,
    )
    assert picked == [20, 30]  # never, then oldest


def test_pick_random_deterministic():
    ids = pick_random_client_ids([1, 2, 3, 4, 5], n=2, rng=random.Random(0))
    assert len(ids) == 2
    assert set(ids).issubset({1, 2, 3, 4, 5})
    assert set(pick_random_client_ids([1, 2], n=5, rng=random.Random(1))) == {1, 2}


def test_build_concern_queue_ranks_and_snoozes(monkeypatch):
    import models
    import services.portfolio_discussion_service as mod

    assert models.Client is not None

    today = date(2026, 10, 31)
    clients = [
        SimpleNamespace(id=1, name="Alpha"),
        SimpleNamespace(id=2, name="Beta"),
        SimpleNamespace(id=3, name="Gamma"),
        SimpleNamespace(id=4, name="Delta"),
    ]

    class FakeClientQuery:
        def filter_by(self, **kwargs):
            return self

        def order_by(self, *args):
            return self

        def all(self):
            return clients

    FakeClient = type("FakeClient", (), {"name": "name", "query": FakeClientQuery()})
    monkeypatch.setattr(models, "Client", FakeClient)
    monkeypatch.setattr(mod, "portfolio_discussion_table_ready", lambda: True)
    monkeypatch.setattr(
        mod,
        "_discussion_state_map",
        lambda ids: {
            2: {"hide_until": date(2026, 11, 15), "last_reviewed_at": datetime(2026, 10, 1)},
            3: {"hide_until": None, "last_reviewed_at": None},
            1: {"hide_until": None, "last_reviewed_at": datetime(2026, 8, 1)},
            4: {"hide_until": None, "last_reviewed_at": datetime(2026, 9, 1)},
        },
    )

    snaps = {
        1: {"portfolio_xirr_pct": 4.0, "benchmark_xirr_pct": 10.0, "underperformance_pct": 6.0, "comparison_type": "full_portfolio"},
        2: {"portfolio_xirr_pct": 1.0, "benchmark_xirr_pct": 10.0, "underperformance_pct": 9.0, "comparison_type": "full_portfolio"},
        3: {"portfolio_xirr_pct": 12.0, "benchmark_xirr_pct": 10.0, "underperformance_pct": -2.0, "comparison_type": "full_portfolio"},
        4: {"portfolio_xirr_pct": 6.0, "benchmark_xirr_pct": 7.0, "underperformance_pct": 1.0, "comparison_type": "equity_only"},
    }

    monkeypatch.setattr(mod, "_snapshot_for_client", lambda cid: snaps.get(cid))

    data = mod.build_concern_queue(limit=50, as_of=today, rng=random.Random(0))
    ids = [r["client_id"] for r in data["items"]]
    assert 2 not in ids  # snoozed
    assert data["total_concerns"] == 2
    # Concerns first by score: Alpha then Delta
    concern_ids = [r["client_id"] for r in data["items"] if r["pick_reason"] == "concern"]
    assert concern_ids == [1, 4]
    # Gamma is healthy but never checked → aging
    aging_ids = [r["client_id"] for r in data["items"] if r["pick_reason"] == "aging"]
    assert 3 in aging_ids
    assert data["items"][0]["low_xirr"] is True
    assert data["items"][0]["vs_benchmark"] is True


def test_build_queue_mix_includes_aging_and_random(monkeypatch):
    import models
    import services.portfolio_discussion_service as mod

    today = date(2026, 10, 31)
    # 1 concern + several healthy / never-checked so aging+random fill
    clients = [SimpleNamespace(id=i, name=f"C{i}") for i in range(1, 11)]

    class FakeClientQuery:
        def filter_by(self, **kwargs):
            return self

        def order_by(self, *args):
            return self

        def all(self):
            return clients

    FakeClient = type("FakeClient", (), {"name": "name", "query": FakeClientQuery()})
    monkeypatch.setattr(models, "Client", FakeClient)
    monkeypatch.setattr(mod, "portfolio_discussion_table_ready", lambda: True)

    state = {i: {"hide_until": None, "last_reviewed_at": datetime(2026, 10, i)} for i in range(1, 11)}
    state[5]["last_reviewed_at"] = None  # never
    state[6]["last_reviewed_at"] = datetime(2025, 1, 1)  # oldest
    monkeypatch.setattr(mod, "_discussion_state_map", lambda ids: state)

    def snap(cid):
        if cid == 1:
            return {
                "portfolio_xirr_pct": 3.0,
                "benchmark_xirr_pct": 10.0,
                "underperformance_pct": 7.0,
                "comparison_type": "full_portfolio",
            }
        return {
            "portfolio_xirr_pct": 12.0,
            "benchmark_xirr_pct": 10.0,
            "underperformance_pct": -2.0,
            "comparison_type": "full_portfolio",
        }

    monkeypatch.setattr(mod, "_snapshot_for_client", snap)

    data = mod.build_concern_queue(limit=10, as_of=today, rng=random.Random(42))
    reasons = {r["pick_reason"] for r in data["items"]}
    assert "concern" in reasons
    assert "aging" in reasons
    assert data["counts"]["concern"] >= 1
    # Never-checked (5) should beat recently checked for aging
    aging = [r for r in data["items"] if r["pick_reason"] == "aging"]
    assert aging[0]["client_id"] == 5
    assert aging[0]["never_checked"] is True
