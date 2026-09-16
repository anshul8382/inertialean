"""
Tax optimiser global defaults: prefer **active** `tax_optimiser_tax_rule` row when present;
otherwise legacy singleton `tax_optimiser_settings` (id=1).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from extensions import db

from services.ltcg_eligibility import LTCG_MODE_HOLDING_DAYS, LTCG_MODE_TWELVE_MONTHS


def _is_tax_rule_row(row) -> bool:
    return getattr(row, "__tablename__", None) == "tax_optimiser_tax_rule"


def _singleton_get_or_create():
    from models import TaxOptimiserSettings

    row = TaxOptimiserSettings.query.get(1)
    if row is None:
        row = TaxOptimiserSettings(id=1)
        db.session.add(row)
        db.session.commit()
    return row


def get_effective_tax_parameters_row():
    """ORM row (TaxOptimiserTaxRule or TaxOptimiserSettings) used for all getters below."""
    from services.tax_optimiser_tax_rules_service import get_effective_row_for_calculations

    r = get_effective_row_for_calculations()
    if r is not None:
        return r
    return _singleton_get_or_create()


def get_or_create_settings():
    """Backward-compatible name: returns active tax rule row or legacy singleton."""
    return get_effective_tax_parameters_row()


def get_equity_rates_for_report(_fy_start_year: int) -> Dict[str, float]:
    """Rates used in enhanced tax report (LTCG exemption headroom, etc.)."""
    s = get_effective_tax_parameters_row()
    return {
        "stcg_rate": float(s.default_stcg_rate or 0.0),
        "ltcg_rate": float(s.default_ltcg_rate or 0.0),
        "ltcg_exemption_limit": float(s.default_ltcg_exemption_limit or 0.0),
    }


def get_ltcg_holding_days() -> int:
    s = get_effective_tax_parameters_row()
    d = int(s.ltcg_holding_days or 365)
    return max(1, min(d, 3660))


def get_ltcg_eligibility_mode() -> str:
    s = get_effective_tax_parameters_row()
    m = (getattr(s, "ltcg_eligibility_mode", None) or LTCG_MODE_TWELVE_MONTHS).strip().lower()
    if m == LTCG_MODE_HOLDING_DAYS:
        return LTCG_MODE_HOLDING_DAYS
    return LTCG_MODE_TWELVE_MONTHS


def get_ltcg_eligibility_settings() -> Tuple[str, int]:
    """(mode, min_holding_days for legacy mode)."""
    return get_ltcg_eligibility_mode(), get_ltcg_holding_days()


def get_trade_charges_pct() -> float:
    s = get_effective_tax_parameters_row()
    v = float(getattr(s, "trade_charges_pct", 0.1) or 0.0)
    return max(0.0, min(v, 100.0))


def get_section_94_8_params() -> Dict[str, int]:
    s = get_effective_tax_parameters_row()
    return {
        "months_before": max(1, min(int(s.section_94_8_months_before_record or 3), 36)),
        "months_after": max(1, min(int(s.section_94_8_months_after_record or 9), 60)),
        "carryforward_years": max(1, min(int(s.stcg_loss_carryforward_years or 8), 20)),
    }


def settings_to_api_dict() -> Dict[str, Any]:
    from services.tax_optimiser_tax_rules_service import tax_rule_to_api_dict

    s = get_effective_tax_parameters_row()
    if _is_tax_rule_row(s):
        d = tax_rule_to_api_dict(s)
        d["storage"] = "tax_rule"
        return d
    return {
        "id": s.id,
        "default_stcg_rate": float(s.default_stcg_rate or 0.0),
        "default_ltcg_rate": float(s.default_ltcg_rate or 0.0),
        "default_ltcg_exemption_limit": float(s.default_ltcg_exemption_limit or 0.0),
        "ltcg_holding_days": int(s.ltcg_holding_days or 365),
        "ltcg_eligibility_mode": get_ltcg_eligibility_mode(),
        "trade_charges_pct": float(getattr(s, "trade_charges_pct", 0.1) or 0.1),
        "section_94_8_months_before_record": int(s.section_94_8_months_before_record or 3),
        "section_94_8_months_after_record": int(s.section_94_8_months_after_record or 9),
        "stcg_loss_carryforward_years": int(s.stcg_loss_carryforward_years or 8),
        "notes": (s.notes or "").strip(),
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        "storage": "legacy_settings",
    }


def update_settings_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    from services.tax_optimiser_tax_rules_service import apply_tax_parameters_to_row

    s = get_effective_tax_parameters_row()
    if _is_tax_rule_row(s):
        apply_tax_parameters_to_row(s, payload)
        s.updated_at = datetime.utcnow()
        db.session.commit()
        return settings_to_api_dict()

    if "default_stcg_rate" in payload:
        s.default_stcg_rate = float(_clamp(payload["default_stcg_rate"], 0.0, 1.0))
    if "default_ltcg_rate" in payload:
        s.default_ltcg_rate = float(_clamp(payload["default_ltcg_rate"], 0.0, 1.0))
    if "default_ltcg_exemption_limit" in payload:
        s.default_ltcg_exemption_limit = float(max(0.0, float(payload["default_ltcg_exemption_limit"])))
    if "ltcg_holding_days" in payload:
        s.ltcg_holding_days = int(max(1, min(int(payload["ltcg_holding_days"]), 3660)))
    if "ltcg_eligibility_mode" in payload and payload["ltcg_eligibility_mode"] is not None:
        m = str(payload["ltcg_eligibility_mode"]).strip().lower()
        if m in (LTCG_MODE_TWELVE_MONTHS, LTCG_MODE_HOLDING_DAYS):
            s.ltcg_eligibility_mode = m
    if "trade_charges_pct" in payload:
        s.trade_charges_pct = float(max(0.0, min(float(payload["trade_charges_pct"]), 100.0)))
    if "section_94_8_months_before_record" in payload:
        s.section_94_8_months_before_record = int(max(1, min(int(payload["section_94_8_months_before_record"]), 36)))
    if "section_94_8_months_after_record" in payload:
        s.section_94_8_months_after_record = int(max(1, min(int(payload["section_94_8_months_after_record"]), 60)))
    if "stcg_loss_carryforward_years" in payload:
        s.stcg_loss_carryforward_years = int(max(1, min(int(payload["stcg_loss_carryforward_years"]), 20)))
    if "notes" in payload and payload["notes"] is not None:
        s.notes = str(payload["notes"])[:4000]

    s.updated_at = datetime.utcnow()
    db.session.commit()
    return settings_to_api_dict()


def _clamp(x: Any, lo: float, hi: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def list_asset_class_catalog(include_all: bool = False) -> List[Dict[str, Any]]:
    """Asset classes for portfolio / tax mapping UI.

    By default returns only classes that appear on at least one row in ``security``
    (securities master), so the dropdown matches how holdings are classified.

    Pass ``include_all=True`` for the full ``asset_class`` table (admin / legacy).
    """
    from models import AssetClass, Security

    if include_all:
        rows = AssetClass.query.order_by(AssetClass.name.asc()).all()
        return [{"id": r.id, "name": r.name or ""} for r in rows]

    q = (
        db.session.query(AssetClass)
        .join(Security, Security.asset_class_id == AssetClass.id)
        .distinct()
        .order_by(AssetClass.name.asc())
    )
    rows = q.all()
    return [{"id": r.id, "name": r.name or ""} for r in rows]
