"""Billing manager plan: roadmap payload is Jinja-safe (no dict.items clash)."""

from datetime import date
from pathlib import Path

import pytest
from jinja2 import Environment, BaseLoader

from services.billing_manager_service import BillingManagerService

pytestmark = pytest.mark.no_app


def _sample_due():
    return [
        {
            "agreement_id": 11,
            "client_id": 7,
            "client_name": "Alpha Family",
            "estimated_invoice_value": 250000.0,
            "days_overdue": 40,
            "selection_reason": "high_value_due",
        },
        {
            "agreement_id": 12,
            "client_id": 8,
            "client_name": "Beta Trust",
            "estimated_invoice_value": 80000.0,
            "days_overdue": 12,
            "selection_reason": "balanced_priority",
        },
    ]


def test_roadmap_uses_planned_clients_not_items_key():
    roadmap = BillingManagerService._build_weekly_roadmap(
        _sample_due(), weekly_capacity=5, as_of=date(2026, 9, 11)
    )
    assert len(roadmap) == 1
    week = roadmap[0]
    assert "items" not in week
    assert [c["client_name"] for c in week["planned_clients"]] == [
        "Alpha Family",
        "Beta Trust",
    ]


def test_jinja_can_iterate_planned_clients_on_week_dict():
    roadmap = BillingManagerService._build_weekly_roadmap(
        _sample_due(), weekly_capacity=5, as_of=date(2026, 9, 11)
    )
    env = Environment(loader=BaseLoader())
    html = env.from_string(
        "{% for item in wk.planned_clients %}{{ item.client_name }}|{% endfor %}"
    ).render(wk=roadmap[0])
    assert html == "Alpha Family|Beta Trust|"


def test_jinja_wk_items_is_not_iterable():
    """Regression: dict.items method must not be used as the client list."""
    week = {"planned_clients": [{"client_name": "X"}], "week_index": 1}
    env = Environment(loader=BaseLoader())
    try:
        env.from_string("{% for item in wk.items %}{{ item }}{% endfor %}").render(wk=week)
        raised = False
    except TypeError as exc:
        raised = True
        assert "not iterable" in str(exc)
    assert raised


def test_manager_plan_template_iterates_planned_clients():
    text = Path("templates/invoices/manager_plan.html").read_text(encoding="utf-8")
    assert "wk.planned_clients" in text
    assert "wk.items" not in text


class _Ag:
    signed_date = None
    created_at = None


class _Sched:
    def __init__(self, next_billing_date, last_billing_date=None, billing_start_date=None):
        self.next_billing_date = next_billing_date
        self.last_billing_date = last_billing_date
        self.billing_start_date = billing_start_date


def test_add_billing_periods_half_yearly():
    assert BillingManagerService._add_billing_periods(
        date(2025, 7, 19), "half_yearly"
    ) == date(2026, 1, 19)


def test_stale_next_date_is_replaced_by_last_invoice_plus_frequency():
    """Atul-style: stored next is first cycle (2024-09-11) but last invoice is 2025-07-19."""
    nxt = BillingManagerService._resolve_next_billing_date(
        schedule=_Sched(date(2024, 9, 11), last_billing_date=None),
        last_billed=date(2025, 7, 19),
        frequency="half_yearly",
        agreement=_Ag(),
        as_of=date(2026, 9, 11),
    )
    assert nxt == date(2026, 1, 19)


def test_future_stored_next_is_kept_when_after_last_billed():
    nxt = BillingManagerService._resolve_next_billing_date(
        schedule=_Sched(date(2027, 1, 3), last_billing_date=date(2025, 12, 2)),
        last_billed=date(2025, 12, 2),
        frequency="half_yearly",
        agreement=_Ag(),
        as_of=date(2026, 9, 11),
    )
    assert nxt == date(2027, 1, 3)


def test_effective_last_billed_prefers_later_invoice():
    assert BillingManagerService._effective_last_billed(None, date(2025, 7, 19)) == date(
        2025, 7, 19
    )
    assert BillingManagerService._effective_last_billed(
        date(2024, 3, 11), date(2025, 7, 19)
    ) == date(2025, 7, 19)
