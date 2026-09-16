"""Unit tests for monthly investment report amount selection."""
from types import SimpleNamespace

from services.monthly_investment_report_amounts import (
    completed_amount_for_investment,
    effective_amount_for_investment,
)


def _inv(planned, stage=None, actual=None):
    wf = None
    if stage is not None or actual is not None:
        wf = SimpleNamespace(current_stage=stage, actual_amount=actual, id=1)
    return SimpleNamespace(planned_amount=planned, workflow=wf, client_id=1)


def test_completed_uses_actual_not_planned():
    inv = _inv(planned=100000, stage="COMPLETED", actual=87500)
    assert completed_amount_for_investment(inv) == 87500.0


def test_completed_falls_back_to_planned_when_actual_missing():
    inv = _inv(planned=100000, stage="COMPLETED", actual=None)
    assert completed_amount_for_investment(inv) == 100000.0


def test_pending_funds_uses_planned(monkeypatch):
    inv = _inv(planned=50000, stage="FUNDS", actual=None)
    monkeypatch.setattr(
        "services.monthly_investment_report_amounts.sent_amount_for_workflow",
        lambda wf, cid: 99999.0,
    )
    assert effective_amount_for_investment(inv) == 50000.0


def test_pending_notify_uses_sent_when_no_actual(monkeypatch):
    inv = _inv(planned=50000, stage="NOTIFY", actual=None)
    monkeypatch.setattr(
        "services.monthly_investment_report_amounts.sent_amount_for_workflow",
        lambda wf, cid: 48000.0,
    )
    assert effective_amount_for_investment(inv) == 48000.0


def test_pending_exec_prefers_actual_over_sent(monkeypatch):
    inv = _inv(planned=50000, stage="EXEC", actual=47000)
    monkeypatch.setattr(
        "services.monthly_investment_report_amounts.sent_amount_for_workflow",
        lambda wf, cid: 48000.0,
    )
    assert effective_amount_for_investment(inv) == 47000.0


def test_pending_notify_falls_back_to_planned_without_sent_session(monkeypatch):
    inv = _inv(planned=50000, stage="NOTIFY", actual=None)
    monkeypatch.setattr(
        "services.monthly_investment_report_amounts.sent_amount_for_workflow",
        lambda wf, cid: None,
    )
    assert effective_amount_for_investment(inv) == 50000.0
