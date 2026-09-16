"""Approval gates for onboarding proposals/invoices."""
from types import SimpleNamespace

import pytest

from services.onboarding_approval_service import (
    approve_proposal,
    can_approve,
    get_invoice_approval,
    invoice_may_send_to_client,
    proposal_may_send_to_client,
    required_approver_role,
    set_invoice_approved,
    set_invoice_pending_approval,
    submit_proposal_for_approval,
)

pytestmark = pytest.mark.no_app


def test_required_approver_role():
    advisor = SimpleNamespace(is_admin=False, is_manager=False)
    manager = SimpleNamespace(is_admin=False, is_manager=True)
    admin = SimpleNamespace(is_admin=True, is_manager=True)
    assert required_approver_role(advisor) == "manager"
    assert required_approver_role(manager) == "admin"
    assert required_approver_role(admin) == "admin"


def test_can_approve_hierarchy():
    advisor = SimpleNamespace(id=1, is_admin=False, is_manager=False)
    manager = SimpleNamespace(id=2, is_admin=False, is_manager=True)
    admin = SimpleNamespace(id=3, is_admin=True, is_manager=True)
    assert can_approve(manager, advisor) is True
    assert can_approve(admin, advisor) is True
    assert can_approve(advisor, advisor) is False
    assert can_approve(manager, manager) is False
    assert can_approve(admin, manager) is True
    assert can_approve(manager, admin) is False


def test_invoice_approval_markers():
    inv = SimpleNamespace(notes="")
    set_invoice_pending_approval(inv)
    info = get_invoice_approval(inv)
    assert info["is_pending"] is True
    ok, reason = invoice_may_send_to_client(inv)
    assert ok is False
    assert "pending" in reason.lower() or "approv" in reason.lower()

    set_invoice_approved(inv, approved_by_id=9)
    info = get_invoice_approval(inv)
    assert info["is_approved"] is True
    assert info["approved_by"] == 9
    ok, _ = invoice_may_send_to_client(inv)
    assert ok is True


def test_proposal_approval_flow():
    proposal = SimpleNamespace(status="generated", details_json={}, updated_at=None)
    submit_proposal_for_approval(proposal)
    assert proposal.status == "pending_approval"
    ok, _ = proposal_may_send_to_client(proposal)
    assert ok is False
    approve_proposal(proposal, approved_by_id=5)
    assert proposal.status == "approved"
    assert proposal.details_json.get("approved_by") == 5
    ok, _ = proposal_may_send_to_client(proposal)
    assert ok is True
