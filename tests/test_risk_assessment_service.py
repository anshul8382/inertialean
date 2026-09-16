"""Risk assessment linking rules (no DB)."""
from types import SimpleNamespace
import sys

import pytest

from services import risk_assessment_service as svc

pytestmark = pytest.mark.no_app


class _LeadQuery:
    def __init__(self, lead):
        self._lead = lead

    def get(self, lead_id):
        return self._lead if self._lead and self._lead.id == lead_id else None


def test_match_lead_id_requires_signed_token():
    fake_models = SimpleNamespace(Lead=SimpleNamespace(query=_LeadQuery(None)))
    old_models = sys.modules.get("models")
    sys.modules["models"] = fake_models
    try:
        assert svc._match_lead_id("person@example.com", None) is None
    finally:
        if old_models is None:
            del sys.modules["models"]
        else:
            sys.modules["models"] = old_models


def test_match_lead_id_requires_email_match():
    lead = SimpleNamespace(id=17, email="expected@example.com")
    fake_models = SimpleNamespace(Lead=SimpleNamespace(query=_LeadQuery(lead)))
    old_models = sys.modules.get("models")
    sys.modules["models"] = fake_models
    try:
        assert svc._match_lead_id("other@example.com", 17) is None
        assert svc._match_lead_id("expected@example.com", 17) == 17
    finally:
        if old_models is None:
            del sys.modules["models"]
        else:
            sys.modules["models"] = old_models
