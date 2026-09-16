"""Tests for <<advisory_fee_mode>> Schedule–B prose."""

import pytest

pytestmark = pytest.mark.no_app

from services.agreement_billing_config_service import (
    advisory_model_label,
    format_advisory_fee_mode_block,
)


class _Var:
    def __init__(self, name, value, vtype="text"):
        self.variable_name = name
        self.variable_value = value
        self.variable_type = vtype


class _Ag:
    def __init__(self, variables, agreement_data=None):
        self.variables = variables
        self.agreement_data = agreement_data


def test_advisory_model_label():
    assert "AUA" in advisory_model_label("aua")
    assert "Fixed Fee" in advisory_model_label("fixed_fee")
    assert "first year" in advisory_model_label("fixed_then_aua").lower()


def test_aua_mode_equity_includes_examples():
    ag = _Ag(
        [
            _Var("advisory_model", "aua", "advisory_model"),
            _Var("asset_type_equity", "Equity", "asset_type"),
        ]
    )
    text = format_advisory_fee_mode_block(ag)
    assert "Assets under Advice (AUA) mode" in text
    assert "Equity" in text
    assert "Direct Stocks" in text or "REITs" in text


def test_aua_mode_multi_asset():
    ag = _Ag(
        [
            _Var("advisory_model", "aua", "advisory_model"),
            _Var("asset_type_equity", "Equity", "asset_type"),
            _Var("asset_type_debt", "Debt", "asset_type"),
        ]
    )
    text = format_advisory_fee_mode_block(ag)
    assert "Equity" in text and "Debt" in text


def test_fixed_fee_mode_includes_amount():
    ag = _Ag(
        [
            _Var("advisory_model", "fixed_fee", "advisory_model"),
            _Var("fixed_annual_fee", "100000", "billing_config"),
        ]
    )
    text = format_advisory_fee_mode_block(ag)
    assert "Fixed Fee mode" in text
    assert "100,000" in text or "100000" in text


def test_fixed_then_aua_mentions_first_year():
    ag = _Ag(
        [
            _Var("advisory_model", "fixed_then_aua", "advisory_model"),
            _Var("first_year_fixed_annual_fee", "75000", "billing_config"),
            _Var("asset_type_equity", "Equity", "asset_type"),
        ]
    )
    text = format_advisory_fee_mode_block(ag)
    assert "first year" in text.lower() or "First year" in text
    assert "AUA" in text
