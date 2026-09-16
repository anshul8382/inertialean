"""billing_fee_portfolio_text autofill and persistence."""

import json

from services.agreement_billing_config_service import (
    BILLING_FEE_PORTFOLIO_TEXT_VAR,
    resolve_billing_fee_portfolio_text,
)


class _AC:
    def __init__(self, name):
        self.name = name


class _Row:
    def __init__(self, name, rate_pct, ac_id=1):
        self.asset_class = _AC(name)
        self.asset_class_id = ac_id
        self.min_amount = 0
        self.max_amount = None
        self.rate_percentage = rate_pct / 100.0
        self.min_fee = 0
        self.max_fee = None
        self.is_active = True


class _Var:
    def __init__(self, name, value, vtype="billing_config"):
        self.variable_name = name
        self.variable_value = value
        self.variable_type = vtype


class _Ag:
    def __init__(self):
        self.id = 1
        self.agreement_data = json.dumps({"billing_structure_type": "differential"})
        self.billing_rates = [
            _Row("Equity", 1.0, 1),
            _Row("Debt ETF", 0.5, 2),
        ]
        self.variables = [
            _Var("billing_frequency", "half_yearly"),
            _Var("period_start_month", "1"),
            _Var("valuation_date_rule", "prepaid"),
        ]


def test_resolve_billing_fee_portfolio_text():
    text = resolve_billing_fee_portfolio_text(_Ag())
    assert "Advisory fee structure" in text
    assert "Assets" in text and "Rate" in text
    assert "Equity" in text and "1%" in text
    assert "Debt ETF" in text and "0.5%" in text
    assert "Portfolio valuation dates" not in text
    assert "pre-paid" not in text.lower()
    assert "Fee will be calculated" not in text
