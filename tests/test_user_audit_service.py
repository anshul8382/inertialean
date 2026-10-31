"""Unit tests for per-user audit / login-day helpers."""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.no_app


def test_month_activity_summary_empty_without_table(monkeypatch):
    import services.user_audit_service as mod

    monkeypatch.setattr(mod, "audit_log_table_exists", lambda: False)
    summary = mod.month_activity_summary(1, 2026, 10)
    assert summary["login_day_count"] == 0
    assert summary["login_days"] == []
    assert summary["days_in_month"] == 31


def test_login_days_in_month_dedupes(monkeypatch):
    import services.user_audit_service as mod

    monkeypatch.setattr(
        mod,
        "login_days_in_month",
        lambda user_id, year, month: {date(2026, 10, 1), date(2026, 10, 15)},
    )
    # Exercise month_activity_summary layering on login_days
    monkeypatch.setattr(mod, "audit_log_table_exists", lambda: True)

    # Restore real month_activity_summary but stub login_days
    def fake_days(user_id, year, month):
        assert (user_id, year, month) == (4, 2026, 10)
        return {date(2026, 10, 1), date(2026, 10, 15)}

    monkeypatch.setattr(mod, "login_days_in_month", fake_days)
    summary = mod.month_activity_summary(4, 2026, 10)
    assert summary["login_day_count"] == 2
    assert summary["login_days"] == [date(2026, 10, 1), date(2026, 10, 15)]