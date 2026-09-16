"""Lead delete clears owned onboarding rows (no DB)."""
import pytest

from services import lead_delete_service as svc

pytestmark = pytest.mark.no_app


class _Query:
    def __init__(self, store):
        self.store = store

    def filter_by(self, **kwargs):
        self.store["filter"] = kwargs
        return self

    def delete(self, synchronize_session=False):
        self.store["deleted"] = True
        return 1

    def update(self, values, synchronize_session=False):
        self.store["updated"] = values
        return 1


def test_delete_lead_record_clears_children_then_deletes(monkeypatch):
    kyc, proposal, risk = {}, {}, {}

    monkeypatch.setattr(
        svc,
        "_onboarding_models",
        lambda: (
            type("Kyc", (), {"query": _Query(kyc)}),
            type("Proposal", (), {"query": _Query(proposal)}),
            type("Risk", (), {"query": _Query(risk)}),
        ),
    )
    monkeypatch.setattr(svc, "_has_table", lambda name: True)

    session_deleted = {}

    class _Session:
        def delete(self, obj):
            session_deleted["lead"] = obj

    monkeypatch.setattr(svc, "db", type("DB", (), {"session": _Session()})())

    lead = type("L", (), {"id": 602})()
    svc.delete_lead_record(lead)

    assert kyc["filter"] == {"lead_id": 602}
    assert kyc["deleted"] is True
    assert proposal["deleted"] is True
    assert risk["updated"] == {"lead_id": None}
    assert session_deleted["lead"] is lead
