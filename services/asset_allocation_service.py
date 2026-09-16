"""
Build asset-class recommendation rows for generate/review from live holdings + model.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from utils.asset_allocation import (
    build_non_model_asset_row,
    normalize_model_asset_row,
    portfolio_class_in_model,
    portfolio_weight_for_model_class,
)

logger = logging.getLogger(__name__)


def normalize_asset_recommendation_rows(rows: List[Dict]) -> List[Dict]:
    """Apply zero-target rules to a full asset allocation table (e.g. stale session notes)."""
    normalized: List[Dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row.get('in_model'):
            normalized.append(normalize_model_asset_row(row))
        else:
            normalized.append(
                build_non_model_asset_row(
                    row.get('asset_class', ''),
                    float(row.get('current_value') or 0),
                    float(row.get('current_weight') or 0),
                )
            )
    return normalized


def fix_stale_non_model_rows(rows: List[Dict]) -> List[Dict]:
    """
    Repair legacy rows where outside-model holdings copied current → target.

    Preserves user-edited required_change when present (edit mode).
    """
    fixed: List[Dict] = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get('in_model'):
            fixed.append(row)
            continue
        cw = float(row.get('current_weight') or 0)
        tw = float(row.get('target_weight') or 0)
        cv = float(row.get('current_value') or 0)
        tv = float(row.get('target_value') or 0)
        if cv > 0 and abs(tw - cw) < 1e-6 and abs(tv - cv) < 1:
            repaired = build_non_model_asset_row(row.get('asset_class', ''), cv, cw)
            if row.get('required_change') is not None:
                repaired['required_change'] = float(row.get('required_change') or 0)
            fixed.append(repaired)
        else:
            fixed.append(row)
    return fixed


def apply_asset_row_display_rules(
    rows: List[Dict],
    *,
    edit_mode: bool = False,
) -> List[Dict]:
    """Normalize targets for display; in edit mode keep user allocation deltas where set."""
    if not edit_mode:
        return normalize_asset_recommendation_rows(rows)
    updated: List[Dict] = []
    for row in fix_stale_non_model_rows(rows):
        if isinstance(row, dict) and row.get('in_model'):
            updated.append(normalize_model_asset_row(row))
        else:
            updated.append(row)
    return updated


ALLOCATION_EDITS_MARKER = "ALLOCATION_EDITS_APPLIED:"


def session_has_allocation_edits(notes: Optional[str], pending_flag: bool = False) -> bool:
    """True after Continue-to-securities (or equivalent) persisted advisor class amounts."""
    if pending_flag:
        return True
    return bool(notes and ALLOCATION_EDITS_MARKER in notes)


def saved_required_changes_from_payload(
    dist_name_amounts: Optional[Sequence[Tuple[Any, Any]]] = None,
    notes: Optional[str] = None,
) -> Dict[str, float]:
    """
    Advisor-saved class amounts. AssetClassDistribution wins; notes fill missing classes.

    Keys are normalized display names (e.g. Debt → Fixed Income).
    """
    from services.recommendation_multi_asset_service import normalize_asset_class_name

    saved: Dict[str, float] = {}
    for name, amount in dist_name_amounts or []:
        key = normalize_asset_class_name(name)
        if not key or amount is None:
            continue
        try:
            saved[key] = float(amount)
        except (TypeError, ValueError):
            continue

    if notes and "ASSET_ALLOCATIONS:" in notes:
        match = re.search(r"ASSET_ALLOCATIONS:(.+)", notes)
        if match:
            try:
                notes_rows = json.loads(match.group(1))
            except (json.JSONDecodeError, TypeError):
                notes_rows = []
            for nrow in notes_rows or []:
                if not isinstance(nrow, dict):
                    continue
                key = normalize_asset_class_name(nrow.get("asset_class"))
                if not key or key in saved or nrow.get("required_change") is None:
                    continue
                try:
                    saved[key] = float(nrow["required_change"])
                except (TypeError, ValueError):
                    continue
    return saved


def overlay_saved_required_changes(
    rows: List[Dict],
    saved_by_class: Optional[Dict[str, float]],
) -> List[Dict]:
    """
    Restore advisor-edited allocation amounts after a live model rebuild.

    Keeps live current_value / current_weight and model target columns.
    Overlays required_change + action only.
    """
    from services.recommendation_multi_asset_service import (
        default_action_for_class_change,
        normalize_asset_class_name,
    )

    if not saved_by_class:
        return rows

    lookup: Dict[str, float] = {}
    for name, amount in saved_by_class.items():
        key = normalize_asset_class_name(name)
        if not key:
            continue
        try:
            lookup[key] = float(amount)
        except (TypeError, ValueError):
            continue
    if not lookup:
        return rows

    overlaid: List[Dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        updated = dict(row)
        key = normalize_asset_class_name(updated.get("asset_class"))
        if key in lookup:
            required_change = lookup[key]
            updated["required_change"] = required_change
            updated["action"] = default_action_for_class_change(required_change)
        overlaid.append(updated)
    return overlaid


def merge_model_and_portfolio_asset_rows(
    model_asset_recommendations: List[Dict],
    current_state: Dict,
) -> List[Dict]:
    """
    Combine model allocation rows with non-model portfolio buckets (HOLD).
    Same structure as unified_recommendations generate flow.
    """
    asset_recommendations: List[Dict] = []
    current_allocs = current_state.get('current_asset_allocations') or {}
    current_weights = current_state.get('current_asset_weights') or {}
    model_asset_classes = {rec['asset_class'] for rec in model_asset_recommendations}

    for rec in model_asset_recommendations:
        ac_name = rec['asset_class']
        asset_recommendations.append(
            normalize_model_asset_row({
                'asset_class': ac_name,
                'current_value': rec.get('current_value', 0),
                'current_weight': rec.get('current_weight', 0),
                'target_value': rec.get('target_value', 0),
                'target_weight': rec.get('target_weight', 0),
                'required_change': rec.get('required_change', 0),
                'action': rec.get('action', 'HOLD'),
                'in_model': True,
            })
        )

    for asset_class in set(current_allocs.keys()):
        if asset_class in model_asset_classes:
            continue
        if portfolio_class_in_model(asset_class, model_asset_classes):
            continue
        asset_recommendations.append(
            build_non_model_asset_row(
                asset_class,
                current_allocs.get(asset_class, 0),
                current_weights.get(asset_class, 0),
            )
        )

    return asset_recommendations


def build_asset_recommendations_from_api(
    client_id: int,
    investment_amount: float,
) -> Tuple[Optional[List[Dict]], Optional[Dict], Optional[str]]:
    """
    Stage-1 asset rows from live holdings + client model.

    Returns (asset_recommendations, current_state, error_message).
    """
    from models import Client
    from recommendation_api import recommendation_api
    from unified_recommendation_service import UnifiedRecommendationService

    client = Client.query.get(client_id)
    if not client:
        return None, None, f'Client {client_id} not found'

    result = recommendation_api.generate_asset_allocation_only(
        client_id=client_id,
        investment_amount=investment_amount,
    )
    if not result.get('success'):
        return None, None, result.get('error', 'Failed to generate asset allocations')

    raw_model = result.get('asset_allocations') or []
    model_asset_recommendations = [
        rec for rec in raw_model
        if isinstance(rec, dict) and rec.get('asset_class') is not None
    ]

    service = UnifiedRecommendationService()
    portfolio = service._get_or_create_portfolio(client)
    current_state = service._analyze_current_portfolio(client, portfolio)

    for rec in model_asset_recommendations:
        ac = rec.get('asset_class')
        if ac:
            from utils.asset_allocation import lookup_allocation_value
            rec['current_value'] = lookup_allocation_value(
                ac, current_state.get('current_asset_allocations') or {}
            )
            rec['current_weight'] = portfolio_weight_for_model_class(ac, current_state)

    rows = merge_model_and_portfolio_asset_rows(model_asset_recommendations, current_state)
    return normalize_asset_recommendation_rows(rows), current_state, None


def refresh_model_row_targets(
    asset_recommendations: List[Dict],
    client_id: int,
    investment_amount: float,
) -> Tuple[List[Dict], Optional[Dict], Optional[str]]:
    """
    Replace model rows with fresh API calculation; keep non-model HOLD rows.
    Used when review must not drop user-implemented security totals (edit mode).
    """
    fresh_rows, current_state, err = build_asset_recommendations_from_api(client_id, investment_amount)
    if err or fresh_rows is None:
        return asset_recommendations, current_state, err

    fresh_by_class = {r['asset_class']: r for r in fresh_rows if r.get('in_model')}
    updated: List[Dict] = []
    seen_model = set()

    for row in asset_recommendations:
        ac = row.get('asset_class')
        if row.get('in_model') and ac in fresh_by_class:
            fresh = fresh_by_class[ac]
            merged = dict(row)
            merged['current_value'] = fresh['current_value']
            merged['current_weight'] = fresh['current_weight']
            merged['target_value'] = fresh['target_value']
            merged['target_weight'] = fresh['target_weight']
            # Preserve user/security-derived required_change if present; else use fresh
            if row.get('required_change') is None:
                merged['required_change'] = fresh['required_change']
                merged['action'] = fresh['action']
            updated.append(merged)
            seen_model.add(ac)
        else:
            updated.append(row)

    for ac, fresh in fresh_by_class.items():
        if ac not in seen_model:
            updated.append(fresh)

    return updated, current_state, None
