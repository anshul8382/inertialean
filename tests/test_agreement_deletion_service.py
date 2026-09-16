"""Tests for agreement hard-delete guards."""

import pytest

from extensions import db
from models import Agreement, Invoice, Lead, User


@pytest.fixture
def lead_and_user(app):
    with app.app_context():
        user = User.query.first()
        if not user:
            pytest.skip("No user in DB")
        lead = Lead(name="Delete Test Lead", email="del@test.com", phone="1", status="active")
        db.session.add(lead)
        db.session.commit()
        yield lead, user
        db.session.delete(lead)
        db.session.commit()


def test_block_signed_agreement(app, lead_and_user):
    from services.agreement_deletion_service import agreement_deletion_block_reason

    lead, user = lead_and_user
    with app.app_context():
        ag = Agreement(
            lead_id=lead.id,
            status="signed",
            created_by=user.id,
        )
        db.session.add(ag)
        db.session.commit()
        reason = agreement_deletion_block_reason(ag.id)
        assert reason is not None
        assert "signed" in reason.lower()
        db.session.delete(ag)
        db.session.commit()


def test_allow_draft_no_invoices(app, lead_and_user):
    from services.agreement_deletion_service import (
        agreement_deletion_block_reason,
        delete_agreement,
    )

    lead, user = lead_and_user
    with app.app_context():
        ag = Agreement(
            lead_id=lead.id,
            status="draft",
            created_by=user.id,
        )
        db.session.add(ag)
        db.session.commit()
        ag_id = ag.id
        assert agreement_deletion_block_reason(ag_id) is None
        ok, msg = delete_agreement(ag_id)
        assert ok is True
        assert Agreement.query.get(ag_id) is None


def test_block_when_invoice_linked(app, lead_and_user):
    from services.agreement_deletion_service import agreement_deletion_block_reason

    lead, user = lead_and_user
    with app.app_context():
        from models import Client

        client = Client.query.first()
        if not client:
            pytest.skip("No client in DB")
        ag = Agreement(
            lead_id=lead.id,
            status="generated",
            created_by=user.id,
        )
        db.session.add(ag)
        db.session.flush()
        inv = Invoice(
            agreement_id=ag.id,
            client_id=client.id,
            invoice_number="TEST-DEL-001",
            invoice_date=__import__("datetime").date.today(),
            billing_period_start=__import__("datetime").date.today(),
            billing_period_end=__import__("datetime").date.today(),
            total_amount=100,
            net_amount=100,
            due_date=__import__("datetime").date.today(),
            created_by=user.id,
        )
        db.session.add(inv)
        db.session.commit()
        reason = agreement_deletion_block_reason(ag.id)
        assert reason is not None
        assert "invoice" in reason.lower()
        db.session.delete(inv)
        db.session.delete(ag)
        db.session.commit()
