"""Tests for alert creation freeze policy."""

import pytest

from services.alert_creation_policy import alert_creation_enabled, guard_alert_creation

pytestmark = pytest.mark.no_app


def test_alert_creation_frozen_by_default(monkeypatch):
    monkeypatch.delenv("ALERT_CREATION_ENABLED", raising=False)
    assert alert_creation_enabled() is False
    assert guard_alert_creation("test") is False


def test_alert_creation_can_be_reenabled(monkeypatch):
    monkeypatch.setenv("ALERT_CREATION_ENABLED", "true")
    assert alert_creation_enabled() is True
    assert guard_alert_creation("test") is True
