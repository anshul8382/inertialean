"""Load persisted TaxOptimiserStrategy rows; fall back to in-code defaults if table missing or empty."""
from __future__ import annotations

from typing import Any, Dict, List

_DEFAULT_STRATEGIES: List[Dict[str, Any]] = [
    {
        "code": "book_ltcg_exemption",
        "enabled": True,
        "sort_order": 10,
        "params": {
            "min_net_benefit_inr": 5000,
            "min_tax_saved_to_trade_cost_ratio": 2.0,
            "turnover_sell_buy_multiplier": 2.0,
            "buyback_delay_days": 2,
            "max_securities_per_client": 50,
        },
    },
    {
        "code": "harvest_unrealized_loss",
        "enabled": True,
        "sort_order": 12,
        "params": {
            "min_net_benefit_inr": 5000,
            "min_tax_saved_to_trade_cost_ratio": 2.0,
            "turnover_sell_buy_multiplier": 2.0,
            "prioritize_section_94_8_bonus_window": True,
            "require_fy_realised_gain_for_setoff": True,
        },
    },
    {
        "code": "book_stcg_gains",
        "enabled": True,
        "sort_order": 35,
        "params": {
            "min_net_benefit_inr": 5000,
            "min_tax_saved_to_trade_cost_ratio": 2.0,
            "turnover_sell_buy_multiplier": 2.0,
            "buyback_delay_days": 2,
            "max_stcg_crystallise_inr": 0,
            "min_holding_days_stcg": 0,
            "max_holding_days_stcg": 0,
            "min_days_until_first_ltcg_sell_date": 0,
            "max_days_until_first_ltcg_sell_date": 0,
            "only_nil_cost_bonus_lots": False,
            "exclude_nil_cost_bonus_lots": False,
            "require_fy_stcg_loss_offset": True,
            "max_allowed_incremental_stcg_tax_inr": 0,
        },
    },
]


def _default_params_by_code() -> Dict[str, Dict[str, Any]]:
    return {
        row["code"]: dict(row.get("params") or {})
        for row in _DEFAULT_STRATEGIES
        if row.get("enabled")
    }


def load_enabled_strategy_params() -> Dict[str, Dict[str, Any]]:
    """
    If `tax_optimiser_strategy` has any rows, only **enabled** DB rows apply (merged onto code defaults
    per code). If the table is missing or empty, use in-code defaults.
    """
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from models import TaxOptimiserStrategy

        all_rows = TaxOptimiserStrategy.query.all()
        if all_rows:
            defaults_by_code = _default_params_by_code()
            for r in all_rows:
                if not r.enabled:
                    continue
                base = dict(defaults_by_code.get(r.code) or {})
                out[r.code] = {**base, **(r.params or {})}
            return out
    except Exception:
        pass
    for row in _DEFAULT_STRATEGIES:
        if row.get("enabled"):
            out[row["code"]] = dict(row.get("params") or {})
    return out
