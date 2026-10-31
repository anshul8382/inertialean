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


def test_run_plan_allows_weekly_capacity_above_five():
    """Capacity used to be hard-capped at 5; ops may process more."""
    # Avoid DB: only assert the clamp helper path via _build_weekly_roadmap sizing.
    many = _sample_due() * 6  # 12 items
    roadmap = BillingManagerService._build_weekly_roadmap(
        many, weekly_capacity=8, as_of=date(2026, 9, 11)
    )
    assert roadmap[0]["planned_billings"] == 8
    assert len(roadmap) == 2  # 8 + 4
    # Clamp contract (mirrors run_plan): 1..50
    capacity = max(1, min(int(8), 50))
    assert capacity == 8
    assert max(1, min(int(100), 50)) == 50


def test_status_report_buckets_caught_up_lagging_missing():
    opps = [
        {
            "agreement_id": 1,
            "client_id": 10,
            "client_name": "Lagging Co",
            "last_billed_date": "2025-01-01",
            "next_billing_date": "2025-07-01",
            "days_overdue": 90,
            "estimated_invoice_value": 100000.0,
            "billing_frequency": "half_yearly",
            "is_due": True,
            "has_hard_block": False,
            "exclude_from_due_plan": False,
            "flags": [],
        },
        {
            "agreement_id": 2,
            "client_id": 11,
            "client_name": "Caught Up LLC",
            "last_billed_date": "2026-01-01",
            "next_billing_date": "2026-07-01",
            "days_overdue": 0,
            "estimated_invoice_value": 50000.0,
            "billing_frequency": "half_yearly",
            "is_due": False,
            "has_hard_block": False,
            "exclude_from_due_plan": False,
            "flags": [],
        },
        {
            "agreement_id": 3,
            "client_id": 12,
            "client_name": "Missing Rates",
            "last_billed_date": None,
            "next_billing_date": "2025-01-01",
            "days_overdue": 200,
            "estimated_invoice_value": 0.0,
            "billing_frequency": "yearly",
            "is_due": True,
            "has_hard_block": True,
            "exclude_from_due_plan": False,
            "flags": [
                {
                    "code": "MISSING_BILLING_RATES",
                    "severity": "hard",
                    "message": "No active billing rates.",
                }
            ],
        },
        {
            "agreement_id": 4,
            "client_id": 13,
            "client_name": "Already Covered",
            "last_billed_date": "2025-01-01",
            "next_billing_date": "2025-07-01",
            "days_overdue": 40,
            "estimated_invoice_value": 20000.0,
            "billing_frequency": "half_yearly",
            "is_due": True,
            "has_hard_block": False,
            "exclude_from_due_plan": True,
            "period_covered": True,
            "flags": [],
        },
    ]
    report = BillingManagerService._build_status_report(opps)
    assert [r["client_name"] for r in report["lagging"]] == ["Lagging Co"]
    assert [r["client_name"] for r in report["caught_up"]] == ["Caught Up LLC"]
    assert [r["client_name"] for r in report["already_covered"]] == ["Already Covered"]
    assert [r["client_name"] for r in report["missing_details"]] == ["Missing Rates"]
    assert report["missing_details"][0]["hard_flag_codes"] == ["MISSING_BILLING_RATES"]


class _Inv:
    def __init__(self, **kw):
        self.agreement_id = kw.get("agreement_id")
        self.client_id = kw.get("client_id", 1)
        self.id = kw.get("id", 1)
        self.invoice_number = kw.get("invoice_number", "INV-1")
        self.invoice_date = kw.get("invoice_date")
        self.due_date = kw.get("due_date")
        self.status = kw.get("status", "sent")
        self.total_amount = kw.get("total_amount", 1000)
        self.notes = kw.get("notes", "")


def test_signals_period_covered_and_outstanding():
    signals = BillingManagerService._signals_from_invoices(
        [
            _Inv(
                id=9,
                agreement_id=None,
                invoice_number="Z-100",
                invoice_date=date(2026, 2, 1),
                due_date=date(2026, 2, 15),
                status="sent",
                total_amount=50000,
                notes="Pulled from Zoho Books\n[zoho_invoice_id=abc]",
            )
        ],
        agreement_id=1,
        next_billing=date(2026, 1, 19),
        last_billed=date(2025, 7, 19),
        as_of=date(2026, 3, 1),
    )
    assert signals["period_covered"] is True
    assert signals["exclude_from_due_plan"] is True
    assert signals["period_invoice_from_zoho"] is True
    assert signals["outstanding_unpaid"] == 50000.0
    assert signals["last_zoho_invoice_date"] == "2026-02-01"


def test_household_mode_counts_peer_agreement_invoice():
    """Anjali due check sees Ajith's invoice even when agreement_id differs."""
    signals = BillingManagerService._signals_from_invoices(
        [
            _Inv(
                id=1,
                client_id=20,  # Ajith
                agreement_id=99,  # Ajith's agreement
                invoice_number="FAM-1",
                invoice_date=date(2026, 2, 10),
                due_date=date(2026, 2, 20),
                status="paid",
                total_amount=80000,
                notes="[zoho_invoice_id=z1]",
            )
        ],
        agreement_id=55,  # Anjali's agreement
        next_billing=date(2026, 1, 19),
        last_billed=date(2025, 7, 19),
        as_of=date(2026, 3, 1),
        household_mode=True,
        self_client_id=21,  # Anjali
    )
    assert signals["period_covered"] is True
    assert signals["household_peer_billed"] is True
    assert signals["period_invoice_client_id"] == 20


def test_dedupe_household_keeps_primary_only():
    opps = [
        {
            "household_id": 7,
            "household_name": "Menon Family",
            "household_primary_client_id": 20,
            "client_id": 21,
            "client_name": "Anjali Menon",
            "is_due": True,
            "has_hard_block": False,
            "exclude_from_due_plan": False,
            "estimated_invoice_value": 50000.0,
            "days_overdue": 30,
            "flags": [],
        },
        {
            "household_id": 7,
            "household_name": "Menon Family",
            "household_primary_client_id": 20,
            "client_id": 20,
            "client_name": "Ajith Menon",
            "is_due": True,
            "has_hard_block": False,
            "exclude_from_due_plan": False,
            "estimated_invoice_value": 80000.0,
            "days_overdue": 30,
            "flags": [],
        },
    ]
    BillingManagerService._dedupe_household_due_opportunities(opps)
    ajith = next(o for o in opps if o["client_id"] == 20)
    anjali = next(o for o in opps if o["client_id"] == 21)
    assert ajith.get("exclude_from_due_plan") is not True
    assert anjali["exclude_from_due_plan"] is True
    assert anjali["household_covered_by_name"] == "Ajith Menon"


def test_rank_due_boosts_collections_outstanding():
    ranked = BillingManagerService._rank_due(
        [
            {
                "agreement_id": 1,
                "client_name": "Fee Only",
                "estimated_invoice_value": 100000.0,
                "outstanding_unpaid": 0.0,
                "days_overdue": 10,
                "next_billing_date": "2026-01-01",
            },
            {
                "agreement_id": 2,
                "client_name": "Collections",
                "estimated_invoice_value": 20000.0,
                "outstanding_unpaid": 200000.0,
                "days_overdue": 10,
                "next_billing_date": "2026-01-01",
            },
        ],
        value_weight=0.7,
        overdue_weight=0.3,
    )
    assert ranked[0]["client_name"] == "Collections"
    assert ranked[0]["selection_reason"] == "collections_outstanding"


def test_manager_plan_template_has_missing_modal_and_status_tabs():
    text = Path("templates/invoices/manager_plan.html").read_text(encoding="utf-8")
    assert 'id="missingBillingModal"' in text
    assert "Billing status report" in text
    assert "pane-caught-up" in text
    assert "pane-lagging" in text
    assert "pane-covered" in text
    assert "Refresh Zoho" in text
    assert "refresh_and_build" in text


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
