from services.agreement_billing_config_service import (
    format_billing_period_description,
    resolve_billing_period_text,
)


class _Var:
    def __init__(self, name, value):
        self.variable_name = name
        self.variable_value = value


class _Ag:
    variables = [
        _Var("billing_frequency", "half_yearly"),
        _Var("period_start_month", "1"),
    ]


def test_format_half_yearly_january_anchor():
    text = format_billing_period_description("half_yearly", 1)
    assert "January" in text
    assert "Half-yearly" in text or "half" in text.lower()


def test_resolve_from_agreement_vars():
    text = resolve_billing_period_text(_Ag())
    assert "January" in text
