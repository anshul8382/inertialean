"""Unit tests for services/asset_allocation_service.py"""
import json

import pytest

from services.asset_allocation_service import (
    ALLOCATION_EDITS_MARKER,
    apply_asset_row_display_rules,
    merge_model_and_portfolio_asset_rows,
    overlay_saved_required_changes,
    saved_required_changes_from_payload,
    session_has_allocation_edits,
)

pytestmark = pytest.mark.no_app
from utils.asset_allocation import (
    build_non_model_asset_row,
    normalize_model_asset_row,
)


def test_non_model_row_zero_target_not_current():
  row = build_non_model_asset_row('Gold', 2_000_000.0, 15.0)
  assert row['target_value'] == 0.0
  assert row['target_weight'] == 0.0
  assert row['required_change'] == -2_000_000.0
  assert row['action'] == 'SELL'
  assert row['in_model'] is False


def test_normalize_model_row_zero_percent_target():
  stale = {
      'asset_class': 'Gold',
      'current_value': 1_500_000.0,
      'current_weight': 10.0,
      'target_value': 1_500_000.0,
      'target_weight': 0.0,
      'required_change': 0.0,
      'action': 'HOLD',
      'in_model': True,
  }
  fixed = normalize_model_asset_row(stale)
  assert fixed['target_value'] == 0.0
  assert fixed['target_weight'] == 0.0
  assert fixed['required_change'] == -1_500_000.0
  assert fixed['action'] == 'SELL'


def test_merge_non_model_holdings_do_not_inflate_target_weights():
  model_rows = [
      {
          'asset_class': 'Equity',
          'current_value': 7_000_000.0,
          'current_weight': 70.0,
          'target_value': 7_000_000.0,
          'target_weight': 70.0,
          'required_change': 0.0,
          'action': 'HOLD',
      },
      {
          'asset_class': 'Fixed Income',
          'current_value': 3_000_000.0,
          'current_weight': 30.0,
          'target_value': 3_000_000.0,
          'target_weight': 30.0,
          'required_change': 0.0,
          'action': 'HOLD',
      },
  ]
  current_state = {
      'current_asset_allocations': {
          'Equity': 7_000_000.0,
          'Fixed Income': 3_000_000.0,
          'Gold': 1_000_000.0,
      },
      'current_asset_weights': {
          'Equity': 63.64,
          'Fixed Income': 27.27,
          'Gold': 9.09,
      },
  }
  merged = merge_model_and_portfolio_asset_rows(model_rows, current_state)
  gold = next(r for r in merged if r['asset_class'] == 'Gold')
  assert gold['target_value'] == 0.0
  assert gold['target_weight'] == 0.0
  assert gold['in_model'] is False

  model_target_weight_sum = sum(
      r['target_weight'] for r in merged if r.get('in_model')
  )
  assert model_target_weight_sum == 100.0


def test_merge_model_zero_percent_class():
  model_rows = [
      {
          'asset_class': 'Gold',
          'current_value': 500_000.0,
          'current_weight': 5.0,
          'target_value': 0.0,
          'target_weight': 0.0,
          'required_change': -500_000.0,
          'action': 'SELL',
      },
      {
          'asset_class': 'Equity',
          'current_value': 9_500_000.0,
          'current_weight': 95.0,
          'target_value': 9_500_000.0,
          'target_weight': 100.0,
          'required_change': 0.0,
          'action': 'HOLD',
      },
  ]
  current_state = {
      'current_asset_allocations': {'Gold': 500_000.0, 'Equity': 9_500_000.0},
      'current_asset_weights': {'Gold': 5.0, 'Equity': 95.0},
  }
  merged = merge_model_and_portfolio_asset_rows(model_rows, current_state)
  gold = next(r for r in merged if r['asset_class'] == 'Gold')
  assert gold['target_value'] == 0.0
  assert gold['target_weight'] == 0.0
  assert gold['required_change'] == -500_000.0


def test_overlay_restores_advisor_amounts_after_model_rebuild():
    """FR-WF-RECO-01: edited class amounts survive a live holdings/model rebuild."""
    rebuilt = [
        {
            'asset_class': 'Equity',
            'current_value': 1_098_483.26,
            'current_weight': 55.0,
            'target_value': 1_518_786.61,
            'target_weight': 60.0,
            'required_change': 420_303.35,
            'action': 'BUY',
            'in_model': True,
        },
        {
            'asset_class': 'International',
            'current_value': 93_448.41,
            'current_weight': 5.0,
            'target_value': 0.0,
            'target_weight': 0.0,
            'required_change': -93_448.41,
            'action': 'SELL',
            'in_model': False,
        },
        {
            'asset_class': 'Fixed Income',
            'current_value': 0.0,
            'current_weight': 0.0,
            'target_value': 379_696.65,
            'target_weight': 20.0,
            'required_change': 379_696.65,
            'action': 'BUY',
            'in_model': True,
        },
    ]
    saved = {'Equity': 750_000.0, 'International': 50_000.0, 'Fixed Income': 0.0}
    out = overlay_saved_required_changes(rebuilt, saved)

    equity = next(r for r in out if r['asset_class'] == 'Equity')
    assert equity['required_change'] == 750_000.0
    assert equity['action'] == 'BUY'
    assert equity['current_value'] == 1_098_483.26
    assert equity['target_value'] == 1_518_786.61

    intl = next(r for r in out if r['asset_class'] == 'International')
    assert intl['required_change'] == 50_000.0
    assert intl['action'] == 'BUY'

    fi = next(r for r in out if r['asset_class'] == 'Fixed Income')
    assert fi['required_change'] == 0.0
    assert fi['action'] == 'HOLD'


def test_overlay_after_display_rules_keeps_non_model_edit():
    """Draft display rules reset outside-model rows to sell-all; overlay must win."""
    rows = [
        {
            'asset_class': 'International',
            'current_value': 93_448.41,
            'current_weight': 5.0,
            'target_value': 0.0,
            'target_weight': 0.0,
            'required_change': 50_000.0,
            'action': 'BUY',
            'in_model': False,
        }
    ]
    normalized = apply_asset_row_display_rules(rows, edit_mode=False)
    assert normalized[0]['required_change'] == pytest.approx(-93_448.41)
    restored = overlay_saved_required_changes(normalized, {'International': 50_000.0})
    assert restored[0]['required_change'] == 50_000.0
    assert restored[0]['action'] == 'BUY'


def test_saved_changes_notes_fill_missing_and_alias():
    dists = [('Equity', 750_000.0), ('Debt', 0.0)]
    notes = 'ASSET_ALLOCATIONS:' + json.dumps([
        {'asset_class': 'International', 'required_change': 50_000.0},
        {'asset_class': 'Equity', 'required_change': 1.0},
    ])
    saved = saved_required_changes_from_payload(dists, notes)
    assert saved['Equity'] == 750_000.0
    assert saved['Fixed Income'] == 0.0
    assert saved['International'] == 50_000.0


def test_session_has_allocation_edits_marker():
    assert session_has_allocation_edits(None) is False
    assert session_has_allocation_edits('ASSET_ALLOCATIONS:[]') is False
    assert session_has_allocation_edits(ALLOCATION_EDITS_MARKER + '1') is True
    assert session_has_allocation_edits(None, pending_flag=True) is True
