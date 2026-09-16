"""Tests for fixed-then-AUA billing transition and valuation date labelling."""

from datetime import date, timedelta

from services.agreement_billing_transition_service import (
    ADVISORY_FIXED_THEN_AUA,
    billing_start_date,
    format_fixed_then_aua_special_note,
    format_valuation_date_rule_description,
    resolve_effective_advisory_model,
    valuation_date_for_period,
    years_since_billing_start,
)


class _Var:
    def __init__(self, name, value, vtype="billing_config"):
        self.variable_name = name
        self.variable_value = value
        self.variable_type = vtype


class _Agreement:
    def __init__(self, variables=None, agreement_data=None, signed_date=None, schedules=None):
        self.variables = variables or []
        self.agreement_data = agreement_data
        self.signed_date = signed_date
        self.billing_schedules = schedules or []


def test_resolve_effective_model_year_one_fixed_fee():
    start = date(2024, 4, 1)
    agr = _Agreement(
        variables=[
            _Var("advisory_model", ADVISORY_FIXED_THEN_AUA, "advisory_model"),
            _Var("billing_start_date", start.isoformat()),
        ]
    )
    assert resolve_effective_advisory_model(agr, date(2024, 10, 1)) == "fixed_fee"
    assert resolve_effective_advisory_model(agr, date(2025, 3, 31)) == "fixed_fee"


def test_resolve_effective_model_year_two_aua():
    start = date(2024, 4, 1)
    agr = _Agreement(
        variables=[
            _Var("advisory_model", ADVISORY_FIXED_THEN_AUA, "advisory_model"),
            _Var("billing_start_date", start.isoformat()),
        ]
    )
    assert resolve_effective_advisory_model(agr, date(2025, 4, 1)) == "aua"


def test_years_since_billing_start():
    start = date(2024, 1, 1)
    agr = _Agreement(variables=[_Var("billing_start_date", start.isoformat())])
    assert years_since_billing_start(agr, date(2024, 6, 30)) == 0
    assert years_since_billing_start(agr, date(2025, 1, 2)) == 1


def test_valuation_date_prepaid_vs_postpaid():
    ps = date(2025, 1, 1)
    pe = date(2025, 6, 30)
    prepaid, _ = valuation_date_for_period("prepaid", ps, pe)
    postpaid, _ = valuation_date_for_period("postpaid", ps, pe)
    assert prepaid == date(2024, 12, 31)
    assert postpaid == pe


def test_format_portfolio_valuation_dates_list_jan_prepaid():
    from services.agreement_billing_transition_service import format_portfolio_valuation_dates_list

    text = format_portfolio_valuation_dates_list(
        "prepaid", period_start_month=1, frequency="half_yearly"
    )
    assert "January–June" in text
    assert "31 Dec" in text
    assert "July–December" in text
    assert "30 Jun" in text
    assert "pre-paid" not in text.lower()
    assert "basis" not in text.lower()


def test_format_portfolio_valuation_dates_list_jan_postpaid():
    from services.agreement_billing_transition_service import format_portfolio_valuation_dates_list

    text = format_portfolio_valuation_dates_list(
        "postpaid", period_start_month=1, frequency="half_yearly"
    )
    assert "January–June" in text
    assert "30 Jun" in text
    assert "post-paid" not in text.lower()
    assert "basis" not in text.lower()


def test_format_valuation_as_of_period_clause():
    from services.agreement_billing_transition_service import format_valuation_as_of_period_clause

    text = format_valuation_as_of_period_clause(
        "prepaid", period_start_month=6, frequency="half_yearly"
    )
    assert "as of 31 May for the Jun-Nov half-year" in text
    assert "30 Nov for the Dec-May half-year" in text


def test_format_portfolio_valuation_schedule_half_year_jun_prepaid():
    from services.agreement_billing_transition_service import format_portfolio_valuation_schedule

    text = format_portfolio_valuation_schedule(
        "prepaid", period_start_month=6, frequency="half_yearly"
    )
    assert "31 May" in text
    assert "June–November" in text
    assert "30 Nov" in text
    assert "December–May" in text


def test_format_fixed_then_aua_note_includes_fee_and_valuation():
    start = date(2025, 1, 15)
    agr = _Agreement(
        variables=[
            _Var("billing_start_date", start.isoformat()),
            _Var("valuation_date_rule", "prepaid"),
            _Var("billing_frequency", "half_yearly"),
            _Var("period_start_month", "1"),
        ]
    )
    note = format_fixed_then_aua_special_note(
        agr,
        annual_fee_inr=120000,
        first_year_frequency="yearly",
    )
    assert "120,000" in note
    assert "twelve months" in note.lower()
    assert "AUTO-GENERATED" in note
    assert "31 Dec" in note or "May" in note


def test_billing_start_date_from_signed_date_fallback():
    sd = date(2023, 7, 1)
    agr = _Agreement(signed_date=sd)
    assert billing_start_date(agr) == sd
