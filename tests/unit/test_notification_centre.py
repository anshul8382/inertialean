"""Unit tests for notification centre + working hours."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from services.working_hours import wall_hours_since, working_hours_since
from services.notification_centre_service import IGNORE_NOT_ALLOWED, apply_decision


def test_saturday_counts_sunday_does_not():
    # Friday 10:00 → Monday 10:00 = Fri 14h + Sat 24h + Sun 0 + Mon 10h = 48 working hours
    start = datetime(2026, 9, 18, 10, 0, 0)  # Friday
    end = datetime(2026, 9, 21, 10, 0, 0)  # Monday
    hours = working_hours_since(start, end)
    assert hours == pytest.approx(48.0, abs=0.1)


def test_wall_hours_ignores_calendar():
    start = datetime(2026, 9, 20, 10, 0, 0)  # Sunday
    end = datetime(2026, 9, 20, 11, 30, 0)
    assert wall_hours_since(start, end) == pytest.approx(1.5, abs=0.01)


def test_a3_ignore_not_allowed():
    assert "A3" in IGNORE_NOT_ALLOWED
    assert "F1" in IGNORE_NOT_ALLOWED
    assert "T1" in IGNORE_NOT_ALLOWED
    assert "ATT1" in IGNORE_NOT_ALLOWED
    assert "G6" not in IGNORE_NOT_ALLOWED  # one_off
    assert "H2" not in IGNORE_NOT_ALLOWED  # one_off
    assert "D1" not in IGNORE_NOT_ALLOWED


def test_visible_radar_scope():
    from types import SimpleNamespace

    from services.notification_centre_service import _visible_to_user

    advisor = SimpleNamespace(id=10, is_admin=False, is_manager=False)
    manager = SimpleNamespace(id=3, is_admin=False, is_manager=True)

    assert _visible_to_user(advisor, 10, "A1", scope="mine") is True
    assert _visible_to_user(advisor, 99, "A1", scope="mine") is False
    assert _visible_to_user(advisor, 99, "A2", scope="firm") is False  # not oversight

    assert _visible_to_user(manager, 3, "A1", scope="mine") is True
    assert _visible_to_user(manager, 99, "A1", scope="mine") is False
    assert _visible_to_user(manager, 99, "A2", scope="firm") is True  # radar
    assert _visible_to_user(manager, 99, "G5", scope="firm") is False  # not radar
    assert _visible_to_user(manager, 99, "F1", scope="firm") is True


def test_month_end_helpers():
    from datetime import datetime

    from services.notification_centre_service import _ist_today, _month_end_date

    assert _month_end_date(2026, 9) == __import__("datetime").date(2026, 9, 30)
    assert _month_end_date(2026, 2) == __import__("datetime").date(2026, 2, 28)
    # 2026-09-30 18:30 UTC = 2026-10-01 00:00 IST
    assert _ist_today(datetime(2026, 9, 30, 18, 30, 0)).isoformat() == "2026-10-01"
    assert _ist_today(datetime(2026, 9, 30, 12, 0, 0)).isoformat() == "2026-09-30"


def test_apply_decision_rejects_ignore_when_disabled_table(monkeypatch):
    monkeypatch.setattr(
        "services.notification_centre_service.finding_notification_enabled",
        lambda: False,
    )
    user = MagicMock(id=1)
    result = apply_decision(
        user,
        entity_type="workflow",
        entity_id=1,
        decision="ignored",
        signal_id="A3",
    )
    assert result["ok"] is False
    assert result["error"] == "notification_centre_disabled"


def test_apply_decision_rejects_ignore_for_a3(monkeypatch):
    monkeypatch.setattr(
        "services.notification_centre_service.finding_notification_enabled",
        lambda: True,
    )
    user = MagicMock(id=1)
    result = apply_decision(
        user,
        entity_type="workflow",
        entity_id=99,
        decision="ignored",
        signal_id="A3",
    )
    assert result["ok"] is False
    assert result["error"] == "ignore_not_allowed"


def test_group_g6_batch_by_client():
    from services.notification_centre_service import NotificationItem, group_same_client_signal

    members = []
    for i, (sym, kind, msg) in enumerate(
        [
            ("DIVISLAB", "quantity", "DIVISLAB: quantity recommended 3, executed 6"),
            ("HDFCAMC", "price", "HDFCAMC: price target …"),
            ("NAUKRI", "price", "NAUKRI: price target …"),
        ],
        start=1,
    ):
        details = {}
        if kind == "quantity":
            details = {"quantity": {"expected_qty": 3, "actual_qty": 6, "variance_pct": 100}}
        else:
            details = {"price": {"target_price": 100, "actual_price": 110, "variance_pct": 10}}
        members.append(
            NotificationItem(
                signal_id="G6",
                entity_type="finding",
                entity_id=1000 + i,
                title=f"Ishan Jain — {msg}",
                nudge_text="nudge",
                client_id=24,
                client_name="Ishan Jain",
                severity="warning" if kind == "quantity" else "info",
                urgent=True,
                facts={
                    "group_key": "RECOMMENDATION_MATCH_trade_execution_mismatch_2026_05",
                    "symbol": sym,
                    "mismatch_kind": kind,
                    "message": msg,
                    "details": details,
                },
            )
        )
    # Unrelated single finding should stay separate
    members.append(
        NotificationItem(
            signal_id="G1",
            entity_type="finding",
            entity_id=9,
            title="Other",
            nudge_text="n",
            client_id=24,
            client_name="Ishan Jain",
            facts={"group_key": None},
        )
    )
    out = group_same_client_signal(members)
    g6 = [x for x in out if x.signal_id == "G6"]
    assert len(g6) == 1
    assert g6[0].member_count == 3
    assert "3 trade mismatches" in g6[0].title
    assert any(l.startswith("Quantity:") for l in g6[0].detail_lines)
    assert any(l.startswith("Price:") for l in g6[0].detail_lines)
    assert "DIVISLAB" in " ".join(g6[0].detail_lines)
    assert "HDFCAMC" in " ".join(g6[0].detail_lines)
    assert "NAUKRI" in " ".join(g6[0].detail_lines)
    assert set(g6[0].facts["member_ids"]) == {1001, 1002, 1003}

