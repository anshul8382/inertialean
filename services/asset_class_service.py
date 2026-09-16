"""Create/update/delete asset classes (portfolio taxonomy + tax fields on same row)."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from extensions import db


def _clamp_rate(x: Any, default: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        v = default
    return max(0.0, min(v, 1.0))


def _clamp_exemption(x: Any, default: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        v = default
    return max(0.0, min(v, 1_000_000_000.0))


def apply_tax_fields_from_payload(ac, payload: Dict[str, Any]) -> None:
    """Mutate AssetClass ORM row from tax-optimiser / admin payload keys."""
    if "ruleset" in payload and payload["ruleset"] is not None:
        ac.tax_ruleset = str(payload["ruleset"] or "equity").strip().lower()[:32] or "equity"
    if "stcg_rate" in payload:
        ac.tax_stcg_rate = _clamp_rate(payload.get("stcg_rate"), 0.20)
    if "ltcg_rate" in payload:
        ac.tax_ltcg_rate = _clamp_rate(payload.get("ltcg_rate"), 0.125)
    if "ltcg_exemption_limit" in payload:
        ac.tax_ltcg_exemption_limit = _clamp_exemption(
            payload.get("ltcg_exemption_limit"), 125_000.0
        )
    if "ltcg_minimum_months" in payload:
        try:
            ac.tax_ltcg_minimum_months = int(
                max(1, min(int(payload.get("ltcg_minimum_months", 12)), 600))
            )
        except (TypeError, ValueError):
            ac.tax_ltcg_minimum_months = 12
    if "ltcg_eligibility_mode" in payload:
        mode_raw = payload.get("ltcg_eligibility_mode")
        if mode_raw in (None, "", "inherit"):
            ac.tax_ltcg_eligibility_mode = None
        else:
            m = str(mode_raw).strip().lower()
            ac.tax_ltcg_eligibility_mode = m if m in ("twelve_months", "holding_days") else None
    if "ltcg_holding_days" in payload:
        raw = payload.get("ltcg_holding_days")
        if raw is not None and str(raw).strip() != "":
            try:
                ac.tax_ltcg_holding_days = int(max(1, min(int(raw), 3660)))
            except (TypeError, ValueError):
                ac.tax_ltcg_holding_days = None
        else:
            ac.tax_ltcg_holding_days = None
    if "is_active" in payload:
        ac.tax_rules_active = bool(payload.get("is_active", True))
    if "notes" in payload:
        ac.tax_notes = str(payload["notes"])[:8000] if payload.get("notes") is not None else None


def create_asset_class(
    *,
    user_id: int,
    name: str,
    description: Optional[str] = None,
    tax_payload: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Optional[str]]:
    """Returns (AssetClass, error_message)."""
    from models import AssetClass

    name = (name or "").strip()
    if len(name) < 2:
        return None, "Name is required (at least 2 characters)."
    if AssetClass.query.filter(db.func.lower(AssetClass.name) == name.lower()).first():
        return None, "An asset class with this name already exists."

    ac = AssetClass(
        name=name[:100],
        description=(description or "").strip()[:4000] or None,
        created_by=int(user_id),
    )
    tax = tax_payload if isinstance(tax_payload, dict) else {}
    apply_tax_fields_from_payload(
        ac,
        {
            "ruleset": tax.get("ruleset", "equity"),
            "stcg_rate": tax.get("stcg_rate", 0.20),
            "ltcg_rate": tax.get("ltcg_rate", 0.125),
            "ltcg_exemption_limit": tax.get("ltcg_exemption_limit", 125_000.0),
            "ltcg_minimum_months": tax.get("ltcg_minimum_months", 12),
            "ltcg_eligibility_mode": tax.get("ltcg_eligibility_mode"),
            "ltcg_holding_days": tax.get("ltcg_holding_days"),
            "is_active": tax.get("is_active", True),
            "notes": tax.get("notes"),
        },
    )
    db.session.add(ac)
    db.session.commit()
    return ac, None


def update_asset_class(
    asset_class_id: int,
    *,
    name: str,
    description: Optional[str] = None,
    tax_payload: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Optional[str]]:
    from models import AssetClass

    ac = AssetClass.query.get(asset_class_id)
    if not ac:
        return None, "Asset class not found."
    name = (name or "").strip()
    if len(name) < 2:
        return None, "Name is required."
    dup = (
        AssetClass.query.filter(
            db.func.lower(AssetClass.name) == name.lower(),
            AssetClass.id != asset_class_id,
        ).first()
    )
    if dup:
        return None, "Another asset class already uses this name."
    ac.name = name[:100]
    ac.description = (description or "").strip()[:4000] or None
    if tax_payload is not None:
        apply_tax_fields_from_payload(ac, tax_payload)
    db.session.commit()
    return ac, None


def delete_asset_class(asset_class_id: int) -> Optional[str]:
    from models import AssetClass

    ac = AssetClass.query.get(asset_class_id)
    if not ac:
        return "Asset class not found."
    try:
        db.session.delete(ac)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return "Cannot delete: this asset class is still referenced (e.g. securities or models)."
    return None
