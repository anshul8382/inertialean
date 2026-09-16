"""Tests for agreement date formatting and substitution."""

from datetime import date, datetime

from services.agreement_date_service import (
    apply_date_substitutions,
    format_agreement_date_display,
    is_literal_date_placeholder,
    resolve_agreement_date,
)


class _Var:
    def __init__(self, name, value):
        self.variable_name = name
        self.variable_value = value


class _Ag:
    def __init__(self, signed=None, sent=None, variables=None):
        self.signed_date = signed
        self.sent_date = sent
        self.variables = variables or []


def test_format_agreement_date_display_ordinal():
    assert format_agreement_date_display(date(2025, 8, 26)) == "26th Aug 2025"
    assert format_agreement_date_display(date(2025, 8, 1)) == "1st Aug 2025"


def test_resolve_from_signed_date():
    ag = _Ag(signed=datetime(2025, 8, 26, 12, 0))
    assert resolve_agreement_date(ag) == date(2025, 8, 26)


def test_literal_date_placeholder_detected():
    assert is_literal_date_placeholder("26th Aug 2025")
    assert not is_literal_date_placeholder("agreement_date")


def test_apply_fills_literal_date_key():
    ag = _Ag(signed=datetime(2025, 8, 26))
    data = {}
    apply_date_substitutions(ag, {"26th Aug 2025", "agreement_date"}, data)
    assert data["26th Aug 2025"] == "26th Aug 2025"
    assert data["agreement_date"] == "26th Aug 2025"
