"""Unit tests for special_note_parser heuristics."""

import pytest

from services.special_note_parser import (
    diff_against_current,
    parse_special_note,
    resolve_annual_rate_pct_for_storage,
)


def test_half_yearly_aua_rate():
    note = (
        "AUA billing frequency Half Yearly @ 0.25%. "
        "Calculation periods Jul-Dec and Jan-Jun."
    )
    p = parse_special_note(note)
    assert p.billing_frequency == "half_yearly"
    assert p.period_start_month == 1
    assert p.advisory_model == "aua"
    assert p.annual_rate_pct == 0.25
    assert p.confidence in ("medium", "high")


def test_fixed_fee_inr():
    note = "The first invoice will be for ₹10,000 for one year. From next year, billing based on AUA."
    p = parse_special_note(note)
    assert p.fixed_annual_fee_inr == 10000.0
    assert "mixed_aua_and_fixed" in p.flags or p.advisory_model in ("fixed_fee", "aua")


def test_quarterly():
    p = parse_special_note("Quarterly advisory fee at 0.5% on AUA.")
    assert p.billing_frequency == "quarterly"
    assert p.annual_rate_pct == 0.5


def test_diff_gaps():
    p = parse_special_note("Half yearly @ 1% on AUA.")
    d = diff_against_current(
        p,
        {"billing_frequency": None, "rate_pcts": [], "advisory_model": "aua"},
    )
    assert "billing_frequency" in d["gaps"]
    assert "billing_rates" in d["gaps"]


def test_empty():
    p = parse_special_note("")
    assert p.confidence == "low"
    assert "empty_note" in p.flags


def test_resolve_yearly_over_half_yearly_at():
    note = (
        "Yearly Investment advisory Fee : 1 % of AUA\n"
        "Billing Frequency : Half Yearly@0.5% of AUA"
    )
    p = parse_special_note(note)
    annual = resolve_annual_rate_pct_for_storage(note, p)
    assert annual == 1.0
