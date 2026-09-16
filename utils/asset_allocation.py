"""
Asset allocation helpers: alias-aware model-covered value and current lookups.

Used by recommendation_api, unified_recommendation_service, and review routes
so Debt/Fixed Income (and REIT variants) stay consistent across generate and review.
"""
from __future__ import annotations

from typing import Dict, Iterable, Set

from utils.security_asset_class import asset_classes_equivalent

_DEBT_ALIASES = frozenset({'Debt', 'Fixed Income', 'FIXED INCOME'})
_REIT_ALIASES = frozenset({'REITs', 'REIT/InvIT', 'REIT/INVIT', 'REIT'})


def portfolio_class_matches_model(portfolio_class: str, model_class: str) -> bool:
    """True when a portfolio bucket key matches a model asset class name."""
    if not portfolio_class or not model_class:
        return False
    if portfolio_class == model_class:
        return True
    if portfolio_class.lower() == model_class.lower():
        return True
    return asset_classes_equivalent(portfolio_class, model_class)


def portfolio_class_in_model(portfolio_class: str, model_class_names: Iterable[str]) -> bool:
    return any(portfolio_class_matches_model(portfolio_class, mc) for mc in model_class_names)


def sum_model_covered_value(
    current_asset_allocations: Dict[str, float],
    model_class_names: Iterable[str],
) -> float:
    """Sum holdings that belong to model asset classes (alias-aware)."""
    model_set = set(model_class_names or [])
    if not model_set:
        return 0.0
    total = 0.0
    for portfolio_class, value in (current_asset_allocations or {}).items():
        if portfolio_class_in_model(portfolio_class, model_set):
            total += float(value or 0)
    return total


def lookup_allocation_value(asset_class_name: str, current_asset_allocations: Dict[str, float]) -> float:
    """Current rupee value for a model asset class, matching portfolio alias keys."""
    allocations = current_asset_allocations or {}
    if not asset_class_name:
        return 0.0

    direct = float(allocations.get(asset_class_name, 0) or 0)
    if direct > 0:
        return direct

    for key, val in allocations.items():
        if key and key.upper() == asset_class_name.upper():
            fv = float(val or 0)
            if fv > 0:
                return fv

    if asset_class_name in _DEBT_ALIASES:
        for alt in _DEBT_ALIASES:
            fv = float(allocations.get(alt, 0) or 0)
            if fv > 0:
                return fv

    if asset_class_name in _REIT_ALIASES:
        for alt in _REIT_ALIASES:
            fv = float(allocations.get(alt, 0) or 0)
            if fv > 0:
                return fv

    for key, val in allocations.items():
        if asset_classes_equivalent(asset_class_name, key):
            fv = float(val or 0)
            if fv > 0:
                return fv

    return 0.0


def portfolio_weight_for_model_class(asset_class_name: str, current_state: Dict) -> float:
    """Current weight (% of total portfolio) for a model asset class."""
    total = float(current_state.get('total_portfolio_value') or 0)
    if total <= 0:
        return 0.0
    current_value = lookup_allocation_value(
        asset_class_name,
        current_state.get('current_asset_allocations') or {},
    )
    return (current_value / total) * 100.0


def action_from_required_change(required_change: float, sell_threshold: float = 1000.0) -> str:
    """BUY / SELL / HOLD from net allocation delta (matches recommendation_api thresholds)."""
    if required_change > 0:
        return 'HOLD' if abs(required_change) < 1 else 'BUY'
    if required_change < 0:
        return 'HOLD' if abs(required_change) < sell_threshold else 'SELL'
    return 'HOLD'


def build_non_model_asset_row(
    asset_class: str,
    current_value: float,
    current_weight: float,
) -> Dict:
    """
    Portfolio bucket outside the client's asset model (or 0% target).

    Target is 0 — do not copy current holdings into target fields (that inflates totals past 100%).
    """
    cv = float(current_value or 0)
    cw = float(current_weight or 0)
    required_change = -cv
    return {
        'asset_class': asset_class,
        'current_value': cv,
        'current_weight': cw,
        'target_value': 0.0,
        'target_weight': 0.0,
        'required_change': required_change,
        'action': action_from_required_change(required_change),
        'in_model': False,
    }


def normalize_model_asset_row(rec: Dict) -> Dict:
    """
    Ensure model rows with 0% target never show current holdings as the target.

    Guards against stale session data or upstream bugs copying current → target.
    """
    out = dict(rec)
    if not out.get('in_model'):
        return out
    tw = float(out.get('target_weight') or 0)
    if tw != 0:
        return out
    cv = float(out.get('current_value') or 0)
    out['target_value'] = 0.0
    rc = float(out.get('required_change') or 0)
    if abs(rc) < 1e-9 and cv != 0:
        out['required_change'] = -cv
    out['action'] = action_from_required_change(float(out.get('required_change') or 0))
    return out
