"""
Multi-asset recommendation editor helpers (FR-WF-RECO-01).

Security weights are always % of that security's asset class, never % of the
whole portfolio. Section 1 merges are incremental so adding a name or editing
one class cannot drop another class's rows.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from utils.asset_allocation import action_from_required_change, lookup_allocation_value

_DEBT = frozenset({"DEBT", "FIXED INCOME"})
_REIT = frozenset({"REIT/INVIT", "REITS", "REIT", "REIT/INVIT"})


def normalize_asset_class_name(name: Optional[str]) -> str:
    n = (name or "").strip()
    if not n:
        return ""
    upper = n.upper()
    if upper in _DEBT:
        return "Fixed Income"
    if upper in _REIT:
        return "REITs"
    return n


def parse_selected_asset_classes(*sources: Any) -> List[str]:
    """Parse selected class names from query strings, lists, or comma-separated text."""
    names: List[str] = []
    seen: Set[str] = set()

    def _add(raw: Any) -> None:
        if raw is None:
            return
        if isinstance(raw, (list, tuple, set)):
            for item in raw:
                _add(item)
            return
        text = str(raw).strip()
        if not text:
            return
        parts = [p.strip() for p in text.split(",")] if "," in text else [text]
        for part in parts:
            key = normalize_asset_class_name(part)
            if key and key not in seen:
                seen.add(key)
                names.append(key)

    for source in sources:
        _add(source)
    return names


def weight_within_class(security_value: float, asset_class_total: float) -> float:
    """Security weight as % of its asset class. Never uses portfolio total."""
    value = float(security_value or 0)
    total = float(asset_class_total or 0)
    if value == 0 or total == 0:
        return 0.0
    return (value / total) * 100.0


def future_weight_within_class(
    current_value: float,
    amount: float,
    asset_class_total: float,
    class_investment: float,
) -> float:
    """Future security weight as % of future asset-class total."""
    future_value = float(current_value or 0) + float(amount or 0)
    future_total = float(asset_class_total or 0) + float(class_investment or 0)
    if future_total == 0:
        return 0.0
    return (future_value / future_total) * 100.0


def class_total_from_state(asset_class_name: str, current_state: Optional[Dict]) -> float:
    allocations = (current_state or {}).get("current_asset_allocations") or {}
    key = normalize_asset_class_name(asset_class_name) or (asset_class_name or "")
    return float(lookup_allocation_value(key, allocations) or 0)


def _security_key(rec: Dict) -> Optional[int]:
    raw = rec.get("security_id")
    try:
        sid = int(raw)
    except (TypeError, ValueError):
        return None
    return sid if sid else None


def merge_section1_recommendations(
    existing: Sequence[Dict],
    incoming: Sequence[Dict],
    *,
    selected_classes: Optional[Iterable[str]] = None,
    replace_selected: bool = False,
) -> List[Dict]:
    """
    Merge incoming Section 1 rows into existing without dropping other classes.

    Default (replace_selected=False): upsert by security_id; keep existing rows
    that are not in incoming. This is the add-security path.

    replace_selected=True: drop existing rows in selected_classes that are not
    in incoming (recalculate / exclusions). Other classes are always kept.
    """
    selected_norm = {
        normalize_asset_class_name(c) for c in (selected_classes or []) if normalize_asset_class_name(c)
    }

    existing_list = [dict(r) for r in (existing or []) if isinstance(r, dict)]
    incoming_list = [dict(r) for r in (incoming or []) if isinstance(r, dict)]
    incoming_by_id = {}
    for rec in incoming_list:
        sid = _security_key(rec)
        if sid:
            incoming_by_id[sid] = rec

    merged: List[Dict] = []
    seen_ids: Set[int] = set()

    for rec in existing_list:
        sid = _security_key(rec)
        rec_class = normalize_asset_class_name(rec.get("asset_class"))
        in_selected = (not selected_norm) or (rec_class in selected_norm)

        if sid and sid in incoming_by_id:
            updated = dict(rec)
            updated.update(incoming_by_id[sid])
            if not updated.get("asset_class"):
                updated["asset_class"] = rec.get("asset_class")
            merged.append(updated)
            seen_ids.add(sid)
            continue

        if replace_selected and in_selected:
            continue
        merged.append(rec)
        if sid:
            seen_ids.add(sid)

    for rec in incoming_list:
        sid = _security_key(rec)
        if sid and sid in seen_ids:
            continue
        merged.append(rec)
        if sid:
            seen_ids.add(sid)

    return merged


def signed_section1_sum(rows: Iterable[Dict]) -> float:
    total = 0.0
    for rec in rows or []:
        if not isinstance(rec, dict):
            continue
        total += float(rec.get("amount") or 0)
    return total


def action_badge_class(action: Optional[str]) -> str:
    a = (action or "HOLD").strip().upper()
    if a == "BUY":
        return "success"
    if a == "SELL":
        return "danger"
    return "secondary"


def default_action_for_class_change(required_change: float) -> str:
    return action_from_required_change(required_change)


def build_session_asset_row(
    asset_class: str,
    current_value: float,
    current_weight: float,
    required_change: float,
    *,
    in_model: bool = True,
    target_weight: float = 0.0,
) -> Dict[str, Any]:
    target_value = float(current_value or 0) + float(required_change or 0)
    return {
        "asset_class": normalize_asset_class_name(asset_class) or asset_class,
        "current_value": float(current_value or 0),
        "current_weight": float(current_weight or 0),
        "target_value": target_value,
        "target_weight": float(target_weight or 0),
        "required_change": float(required_change or 0),
        "action": default_action_for_class_change(float(required_change or 0)),
        "in_model": bool(in_model),
    }
