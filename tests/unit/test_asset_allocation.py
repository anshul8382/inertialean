"""Unit tests for utils/asset_allocation.py"""
import pytest

from utils.asset_allocation import (
    action_from_required_change,
    build_non_model_asset_row,
    lookup_allocation_value,
    normalize_model_asset_row,
    portfolio_class_in_model,
    portfolio_class_matches_model,
    sum_model_covered_value,
)

pytestmark = pytest.mark.no_app


def test_debt_matches_fixed_income_model():
    assert portfolio_class_matches_model('Debt', 'Fixed Income') is True
    assert portfolio_class_in_model('Debt', {'Fixed Income', 'Equity'}) is True


def test_sum_model_covered_includes_debt_when_model_has_fixed_income():
    allocations = {
        'Debt': 13_000_000.0,
        'Equity': 7_000_000.0,
        'Gold': 2_000_000.0,
    }
    model_classes = {'Fixed Income', 'Equity', 'Gold', 'International', 'REITs', 'Equity ETF'}
    covered = sum_model_covered_value(allocations, model_classes)
    assert covered == 13_000_000.0 + 7_000_000.0 + 2_000_000.0


def test_lookup_allocation_value_debt_alias():
    allocations = {'Debt': 5_000_000.0, 'Equity': 3_000_000.0}
    assert lookup_allocation_value('Fixed Income', allocations) == 5_000_000.0


def test_required_change_action_sell_for_large_negative():
    assert action_from_required_change(-8_000_000) == 'SELL'
    assert action_from_required_change(500_000) == 'BUY'
    assert action_from_required_change(-500) == 'HOLD'


def test_asset_allocation_row_math_overweight_fixed_income():
    """Overweight Fixed Income → negative required_change (SELL)."""
    allocations = {'Fixed Income': 13_784_814.73, 'Equity': 7_109_360.85}
    model_classes = {'Fixed Income', 'Equity'}
    model_covered = sum_model_covered_value(allocations, model_classes)
    target_weight = 18.19
    investment = 0.0
    target_model_covered = model_covered + investment
    current = lookup_allocation_value('Fixed Income', allocations)
    target = (target_weight / 100.0) * target_model_covered
    required_change = target - current
    assert required_change < 0
    assert action_from_required_change(required_change) == 'SELL'


def test_build_non_model_asset_row_targets_zero():
    row = build_non_model_asset_row('International', 1_000_000.0, 8.5)
    assert row['target_value'] == 0.0
    assert row['target_weight'] == 0.0


def test_normalize_model_asset_row_fixes_stale_target():
    row = normalize_model_asset_row({
        'asset_class': 'REITs',
        'current_value': 200_000.0,
        'target_value': 200_000.0,
        'target_weight': 0.0,
        'required_change': 0.0,
        'in_model': True,
    })
    assert row['target_value'] == 0.0
    assert row['required_change'] == -200_000.0
