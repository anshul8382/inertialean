"""Annual risk refresh due helper."""
from datetime import datetime, timedelta

import pytest

from services.client_risk_refresh_service import risk_refresh_due

pytestmark = pytest.mark.no_app


def test_risk_refresh_due_when_never_updated():
    client = type("C", (), {"risk_profile_updated_at": None})()
    assert risk_refresh_due(client) is True


def test_risk_refresh_due_when_old():
    client = type(
        "C",
        (),
        {"risk_profile_updated_at": datetime.utcnow() - timedelta(days=400)},
    )()
    assert risk_refresh_due(client) is True


def test_risk_refresh_not_due_when_recent():
    client = type(
        "C",
        (),
        {"risk_profile_updated_at": datetime.utcnow() - timedelta(days=30)},
    )()
    assert risk_refresh_due(client) is False
