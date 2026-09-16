"""
Multiple named global tax parameter sets; exactly one row is **active** and drives
capital-gains / tax optimiser calculations (via tax_optimiser_settings_service).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from extensions import db

from services.ltcg_eligibility import LTCG_MODE_HOLDING_DAYS, LTCG_MODE_TWELVE_MONTHS


def _model():
    from models import TaxOptimiserTaxRule

    return TaxOptimiserTaxRule


def get_effective_row_for_calculations():
    """
    Active TaxOptimiserTaxRule when the table exists; if no row is flagged active but
    rows exist, use the lowest id (safety). Else None → legacy singleton.
    """
    try:
        TaxOptimiserTaxRule = _model()
        r = TaxOptimiserTaxRule.query.filter_by(is_active=True).first()
        if r is not None:
            return r
        return TaxOptimiserTaxRule.query.order_by(TaxOptimiserTaxRule.id.asc()).first()
    except Exception:
        return None


def tax_rule_to_api_dict(r) -> Dict[str, Any]:
    m = (getattr(r, "ltcg_eligibility_mode", None) or LTCG_MODE_TWELVE_MONTHS).strip().lower()
    if m == LTCG_MODE_HOLDING_DAYS:
        mode = LTCG_MODE_HOLDING_DAYS
    else:
        mode = LTCG_MODE_TWELVE_MONTHS
    return {
        "id": r.id,
        "name": (getattr(r, "name", None) or "").strip() or "Unnamed",
        "sort_order": int(getattr(r, "sort_order", 0) or 0),
        "is_active": bool(getattr(r, "is_active", False)),
        "default_stcg_rate": float(r.default_stcg_rate or 0.0),
        "default_ltcg_rate": float(r.default_ltcg_rate or 0.0),
        "default_ltcg_exemption_limit": float(r.default_ltcg_exemption_limit or 0.0),
        "ltcg_holding_days": int(r.ltcg_holding_days or 365),
        "ltcg_eligibility_mode": mode,
        "trade_charges_pct": float(getattr(r, "trade_charges_pct", 0.1) or 0.1),
        "section_94_8_months_before_record": int(r.section_94_8_months_before_record or 3),
        "section_94_8_months_after_record": int(r.section_94_8_months_after_record or 9),
        "stcg_loss_carryforward_years": int(r.stcg_loss_carryforward_years or 8),
        "notes": ((getattr(r, "notes", None) or "").strip()),
        "updated_at": r.updated_at.isoformat() if getattr(r, "updated_at", None) else None,
    }


def list_tax_rules() -> List[Dict[str, Any]]:
    TaxOptimiserTaxRule = _model()
    rows = (
        TaxOptimiserTaxRule.query.order_by(
            TaxOptimiserTaxRule.sort_order.asc(),
            TaxOptimiserTaxRule.id.asc(),
        ).all()
    )
    return [tax_rule_to_api_dict(r) for r in rows]


def _defaults_from_legacy_singleton():
    from models import TaxOptimiserSettings

    s = TaxOptimiserSettings.query.get(1)
    if s is None:
        return {
            "default_stcg_rate": 0.20,
            "default_ltcg_rate": 0.125,
            "default_ltcg_exemption_limit": 125000.0,
            "ltcg_holding_days": 365,
            "ltcg_eligibility_mode": LTCG_MODE_TWELVE_MONTHS,
            "trade_charges_pct": 0.1,
            "section_94_8_months_before_record": 3,
            "section_94_8_months_after_record": 9,
            "stcg_loss_carryforward_years": 8,
            "notes": "",
        }
    return {
        "default_stcg_rate": float(s.default_stcg_rate or 0.0),
        "default_ltcg_rate": float(s.default_ltcg_rate or 0.0),
        "default_ltcg_exemption_limit": float(s.default_ltcg_exemption_limit or 0.0),
        "ltcg_holding_days": int(s.ltcg_holding_days or 365),
        "ltcg_eligibility_mode": (getattr(s, "ltcg_eligibility_mode", None) or LTCG_MODE_TWELVE_MONTHS)
        .strip()
        .lower(),
        "trade_charges_pct": float(getattr(s, "trade_charges_pct", 0.1) or 0.1),
        "section_94_8_months_before_record": int(s.section_94_8_months_before_record or 3),
        "section_94_8_months_after_record": int(s.section_94_8_months_after_record or 9),
        "stcg_loss_carryforward_years": int(s.stcg_loss_carryforward_years or 8),
        "notes": (s.notes or "").strip(),
    }


def apply_tax_parameters_to_row(row, d: Dict[str, Any]) -> None:
    if "default_stcg_rate" in d:
        row.default_stcg_rate = float(_clamp(d["default_stcg_rate"], 0.0, 1.0))
    if "default_ltcg_rate" in d:
        row.default_ltcg_rate = float(_clamp(d["default_ltcg_rate"], 0.0, 1.0))
    if "default_ltcg_exemption_limit" in d:
        row.default_ltcg_exemption_limit = float(max(0.0, float(d["default_ltcg_exemption_limit"])))
    if "ltcg_holding_days" in d:
        row.ltcg_holding_days = int(max(1, min(int(d["ltcg_holding_days"]), 3660)))
    if "ltcg_eligibility_mode" in d and d["ltcg_eligibility_mode"] is not None:
        m = str(d["ltcg_eligibility_mode"]).strip().lower()
        if m in (LTCG_MODE_TWELVE_MONTHS, LTCG_MODE_HOLDING_DAYS):
            row.ltcg_eligibility_mode = m
    if "trade_charges_pct" in d:
        row.trade_charges_pct = float(max(0.0, min(float(d["trade_charges_pct"]), 100.0)))
    if "section_94_8_months_before_record" in d:
        row.section_94_8_months_before_record = int(max(1, min(int(d["section_94_8_months_before_record"]), 36)))
    if "section_94_8_months_after_record" in d:
        row.section_94_8_months_after_record = int(max(1, min(int(d["section_94_8_months_after_record"]), 60)))
    if "stcg_loss_carryforward_years" in d:
        row.stcg_loss_carryforward_years = int(max(1, min(int(d["stcg_loss_carryforward_years"]), 20)))
    if "notes" in d and d["notes"] is not None:
        row.notes = str(d["notes"])[:4000]


def _clamp(x: Any, lo: float, hi: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def create_tax_rule(payload: Dict[str, Any]) -> Dict[str, Any]:
    TaxOptimiserTaxRule = _model()
    n = TaxOptimiserTaxRule.query.count()
    base = _defaults_from_legacy_singleton()
    active = TaxOptimiserTaxRule.query.filter_by(is_active=True).first()
    if active:
        base = tax_rule_to_api_dict(active)
        del base["id"]
        del base["updated_at"]
    name = (payload.get("name") or "").strip() or f"Rule {n + 1}"
    row = TaxOptimiserTaxRule(
        name=name[:128],
        sort_order=int(payload.get("sort_order") or n),
        is_active=False,
        default_stcg_rate=float(base["default_stcg_rate"]),
        default_ltcg_rate=float(base["default_ltcg_rate"]),
        default_ltcg_exemption_limit=float(base["default_ltcg_exemption_limit"]),
        ltcg_holding_days=int(base["ltcg_holding_days"]),
        ltcg_eligibility_mode=str(base["ltcg_eligibility_mode"]),
        trade_charges_pct=float(base["trade_charges_pct"]),
        section_94_8_months_before_record=int(base["section_94_8_months_before_record"]),
        section_94_8_months_after_record=int(base["section_94_8_months_after_record"]),
        stcg_loss_carryforward_years=int(base["stcg_loss_carryforward_years"]),
        notes=(base.get("notes") or "")[:4000],
    )
    apply_tax_parameters_to_row(row, payload)
    if n == 0:
        row.is_active = True
    db.session.add(row)
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return tax_rule_to_api_dict(row)


def update_tax_rule(rule_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    TaxOptimiserTaxRule = _model()
    row = TaxOptimiserTaxRule.query.get(rule_id)
    if not row:
        raise ValueError("Tax rule not found")
    if "name" in payload and payload["name"] is not None:
        row.name = str(payload["name"]).strip()[:128] or row.name
    if "sort_order" in payload and payload["sort_order"] is not None:
        row.sort_order = int(payload["sort_order"])
    apply_tax_parameters_to_row(row, payload)
    if payload.get("set_active") is True:
        for r in TaxOptimiserTaxRule.query.all():
            r.is_active = False
        row.is_active = True
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return tax_rule_to_api_dict(row)


def delete_tax_rule(rule_id: int) -> None:
    TaxOptimiserTaxRule = _model()
    if TaxOptimiserTaxRule.query.count() <= 1:
        raise ValueError("Cannot delete the only tax rule")
    row = TaxOptimiserTaxRule.query.get(rule_id)
    if not row:
        raise ValueError("Tax rule not found")
    was_active = bool(row.is_active)
    db.session.delete(row)
    db.session.flush()
    if was_active:
        nxt = TaxOptimiserTaxRule.query.order_by(TaxOptimiserTaxRule.id.asc()).first()
        if nxt:
            nxt.is_active = True
    db.session.commit()


def set_active_tax_rule(rule_id: int) -> Dict[str, Any]:
    return update_tax_rule(rule_id, {"set_active": True})
