"""Assigned to column uses client advisor username on the monthly investments report."""

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.no_app


def test_advisor_assigned_to_name_uses_username():
    from daily_monthly_investments_report import _advisor_assigned_to_name

    client = SimpleNamespace(
        advisor=SimpleNamespace(username="priya", email="priya@example.com", id=3)
    )
    assert _advisor_assigned_to_name(client) == "priya"


def test_advisor_assigned_to_name_unassigned_when_no_advisor():
    from daily_monthly_investments_report import _advisor_assigned_to_name

    assert _advisor_assigned_to_name(None) == "Unassigned"
    assert _advisor_assigned_to_name(SimpleNamespace(advisor=None)) == "Unassigned"


def test_completed_section_html_includes_assigned_to_column():
    from daily_monthly_investments_report import _completed_section_html

    investment = SimpleNamespace(
        client=SimpleNamespace(
            name="Test Client",
            advisor=SimpleNamespace(username="anshul", email="a@x.com", id=1),
        ),
        workflow=SimpleNamespace(actual_completion_date=None, actual_amount=1000),
        investment_date=None,
        amount=1000,
        planned_amount=1000,
        sent_amount=None,
        actual_amount=1000,
    )
    html = _completed_section_html([investment])
    assert "<th>Assigned to</th>" in html
    assert ">anshul<" in html
