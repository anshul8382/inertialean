"""Unit tests for Daily Alert Report section builders (ops_digest_email_service)."""
from types import SimpleNamespace

import pytest

from services.ops_digest_email_service import (
    build_alert_report_sections_html,
    classify_alert_section,
    extract_service_ticket_detail,
)

pytestmark = pytest.mark.no_app


def test_extract_service_ticket_detail_from_sla_description():
    alert = SimpleNamespace(
        title="Ticket SLA Breached: TKT-20260404-0002",
        description=(
            "Service ticket TKT-20260404-0002 has breached its SLA deadline. "
            "Ticket: Folio update pending for demat"
        ),
    )
    assert extract_service_ticket_detail(alert) == "Folio update pending for demat"


def test_extract_service_ticket_detail_from_create_description():
    alert = SimpleNamespace(
        title="Service Ticket: TKT-20260415-0001",
        description="KYC mismatch - Client uploaded wrong PAN card",
    )
    assert "KYC mismatch" in extract_service_ticket_detail(alert)


def test_meeting_not_scheduled_omitted_from_report():
    alerts = [
        SimpleNamespace(
            alert_type="communication",
            alert_subtype="meeting_cadence",
            title="Meeting Not Scheduled",
            description="No meeting scheduled",
            severity="warning",
            status="active",
            client=SimpleNamespace(name="Test Client", advisor=None),
            lead=None,
            assigned_user=None,
            current_delay=10,
            created_at=None,
            workflow=None,
        )
    ]
    assert classify_alert_section(alerts[0]) == "meeting_not_scheduled"
    html = build_alert_report_sections_html(alerts, "consolidated", include_advisor_col=True)
    assert "Meeting not scheduled" not in html
    assert "meeting_cadence" not in html.lower() or "No active" in html or "No alerts" in html


def test_lead_sla_shows_lead_name():
    lead = SimpleNamespace(name="Ravi Kumar", user=SimpleNamespace(username="sharveen", email="s@x.com"))
    alerts = [
        SimpleNamespace(
            alert_type="lead_sla",
            alert_subtype="proposal_sent",
            title="SLA Alert: Proposal should be sent within 24 hrs of meeting",
            description="Lead Ravi Kumar moved to proposal_sent stage. Proposal should be sent within 24 hrs of meeting",
            severity="critical",
            status="active",
            client=None,
            lead=lead,
            assigned_user=SimpleNamespace(username="sharveen", email="s@x.com"),
            current_delay=5,
            created_at=None,
            workflow=None,
        )
    ]
    html = build_alert_report_sections_html(alerts, "consolidated", include_advisor_col=True)
    assert "Ravi Kumar" in html
    assert "Lead SLA" in html
    assert "Meeting not scheduled" not in html


def test_service_ticket_section_uses_detail_not_ticket_number_title():
    alerts = [
        SimpleNamespace(
            alert_type="service_ticket",
            alert_subtype="ops",
            title="Service Ticket: TKT-20260415-0001",
            description="Address change - Client relocating to Pune",
            severity="critical",
            status="active",
            client=SimpleNamespace(name="Sowmya Bharath", advisor=SimpleNamespace(username="sharveen", email="s@x.com")),
            lead=None,
            assigned_user=SimpleNamespace(username="sharveen", email="s@x.com"),
            current_delay=48,
            created_at=None,
            workflow=None,
        )
    ]
    html = build_alert_report_sections_html(alerts, "consolidated", include_advisor_col=True)
    assert "Address change - Client relocating to Pune" in html
    assert "Ticket detail" in html
    assert "Service Ticket: TKT-20260415-0001" not in html
