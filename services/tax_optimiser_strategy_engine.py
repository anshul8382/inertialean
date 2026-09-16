"""
Tax optimiser strategy engine: all open-lot cost/gain/MV comes from FIFO replay (`get_open_fifo_lots_with_mtm`).

Book LTCG / Book STCG bundles walk lots in that FIFO list order (oldest consumption globally as produced by
the replay). `suggested_by_security` is sorted for display by estimated net benefit per security
(tax rate × summed gains − blended % on 2× turnover for that security’s lots).

Numeric behaviour is driven by TaxOptimiserStrategy.params (DB) plus TaxOptimiserSettings fallbacks.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from services.capital_gains_service import get_open_fifo_lots_with_mtm
from services.section_94_8_service import (
    client_open_bonus_window_stcg_unrealized_loss_meta,
    load_bonus_actions_by_security,
    open_stcg_unrealized_loss_in_bonus_age_window,
)
from services.tax_optimiser_strategy_loader import load_enabled_strategy_params

# When `defer_to_ltcg` strategy is disabled in DB, unified recommendations still use these for SELL hints.
_DEFAULT_UNIFIED_SELL_TIMING_PARAMS: Dict[str, Any] = {
    "max_days_until_ltcg_eligible": 365,
    "min_unrealized_gain_inr": 0,
}


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _marginal_stcg_tax_inr(net_realized_stcg: float, crystallised_gain: float, stcg_rate: float) -> float:
    """
    Incremental FY STCG tax from crystallising `crystallised_gain` when the client already has
    FY net realized STCG `net_realized_stcg` (gains and losses from sells in the FY already netted).

    Model: taxable STCG base moves from max(0, net) to max(0, net + gain); tax = rate × delta.
    """
    n = float(net_realized_stcg or 0.0)
    g = max(0.0, float(crystallised_gain or 0.0))
    r = max(0.0, float(stcg_rate or 0.0))
    before = max(0.0, n)
    after = max(0.0, n + g)
    return r * (after - before)


def _lot_template_row(lot: Dict[str, Any]) -> Dict[str, Any]:
    pd = lot.get("acquisition_date")
    if isinstance(pd, str):
        pd = date.fromisoformat(pd[:10])
    elif pd is None:
        pd = date.today()
    row: Dict[str, Any] = {
        "security_id": lot.get("security_id"),
        "security_name": lot.get("security_name") or "",
        "security_symbol": lot.get("security_symbol") or "",
        "purchase_date": pd,
        "quantity": float(lot.get("quantity") or 0),
        "cost_basis": round(_safe_float(lot.get("cost_basis")), 2),
        "market_value": round(_safe_float(lot.get("market_value")), 2),
        "gain": round(_safe_float(lot.get("unrealized_pnl")), 2),
        "not_in_model": bool(lot.get("not_in_model")),
    }
    cfc = lot.get("clubbed_fifo_lot_count")
    if cfc is not None and int(cfc) > 1:
        row["clubbed_fifo_lot_count"] = int(cfc)
    return row


def _club_ltcg_open_lots_by_security(open_lots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge consecutive FIFO open lots of the same security into one model trade line
    (different acquisition dates → single sell/buy-back recommendation for that ticker).
    """
    if not open_lots:
        return []
    blocks: List[List[Dict[str, Any]]] = []
    for ln in open_lots:
        sid = int(ln.get("security_id") or 0)
        if not blocks or int(blocks[-1][0].get("security_id") or 0) != sid:
            blocks.append([ln])
        else:
            blocks[-1].append(ln)
    out: List[Dict[str, Any]] = []
    for rows in blocks:
        base = dict(rows[0])
        tg = sum(max(0.0, _safe_float(x.get("unrealized_pnl"))) for x in rows)
        tmv = sum(max(0.0, _safe_float(x.get("market_value"))) for x in rows)
        tqty = sum(max(0.0, _safe_float(x.get("quantity"))) for x in rows)
        tcb = sum(max(0.0, _safe_float(x.get("cost_basis"))) for x in rows)
        not_in = any(bool(x.get("not_in_model")) for x in rows)
        pds: List[date] = []
        for x in rows:
            pd = x.get("acquisition_date")
            if isinstance(pd, str):
                try:
                    pds.append(date.fromisoformat(pd[:10]))
                except ValueError:
                    continue
            elif isinstance(pd, date):
                pds.append(pd)
        earliest = min(pds) if pds else base.get("acquisition_date")
        base["unrealized_pnl"] = tg
        base["market_value"] = tmv
        base["quantity"] = tqty
        base["cost_basis"] = tcb
        base["not_in_model"] = not_in
        base["acquisition_date"] = earliest if earliest is not None else base.get("acquisition_date")
        base["clubbed_fifo_lot_count"] = len(rows)
        out.append(base)
    return out


def _group_by_security(flat: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by: Dict[int, Dict[str, Any]] = {}
    for row in flat:
        sid = int(row.get("security_id") or 0)
        if sid not in by:
            by[sid] = {
                "security_id": sid,
                "security_name": row.get("security_name") or "",
                "security_symbol": row.get("security_symbol") or "",
                "not_in_model": bool(row.get("not_in_model")),
                "overallocated_25": False,
                "model_weight_pct": None,
                "current_weight_pct": None,
                "lots": [],
            }
        by[sid]["lots"].append(row)
        if row.get("not_in_model"):
            by[sid]["not_in_model"] = True
    return list(by.values())


def _is_nil_cost_bonus_style_lot(ln: Dict[str, Any]) -> bool:
    """True when FIFO lot is effectively nil-cost (typical BONUS §55(2)(aa) lot)."""
    cb = _safe_float(ln.get("cost_basis"))
    cpu = _safe_float(ln.get("cost_per_unit"))
    return cb <= 1e-6 and cpu <= 1e-6


def _filter_stcg_gain_lots_for_s2(
    stcg_gain: List[Dict[str, Any]],
    p: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Optional S2 scopes: late-STCG window (holding days / days-to-LTCG), bonus nil-cost only/exclude.

    *min_holding_days_stcg* / *max_holding_days_stcg* approximate “9–12 month” STCG (e.g. 270–364).
    *max_days_until_first_ltcg_sell_date* limits to lots close to LTCG flip.
    """
    out = list(stcg_gain)
    if bool(p.get("only_nil_cost_bonus_lots")):
        out = [ln for ln in out if _is_nil_cost_bonus_style_lot(ln)]
    elif bool(p.get("exclude_nil_cost_bonus_lots")):
        out = [ln for ln in out if not _is_nil_cost_bonus_style_lot(ln)]
    min_h = int(p.get("min_holding_days_stcg") or 0)
    max_h = int(p.get("max_holding_days_stcg") or 0)
    if min_h > 0:
        out = [ln for ln in out if int(ln.get("holding_days") or 0) >= min_h]
    if max_h > 0:
        out = [ln for ln in out if int(ln.get("holding_days") or 0) <= max_h]
    max_d = int(p.get("max_days_until_first_ltcg_sell_date") or 0)
    min_d = int(p.get("min_days_until_first_ltcg_sell_date") or 0)
    if max_d > 0:
        out = [
            ln
            for ln in out
            if 0 < int(ln.get("days_until_first_ltcg_sell_date") or 0) <= max_d
        ]
    if min_d > 0:
        out = [ln for ln in out if int(ln.get("days_until_first_ltcg_sell_date") or 0) >= min_d]
    return out


def _fifo_gain_subset(gain_lots: List[Dict[str, Any]], cap: float) -> List[Dict[str, Any]]:
    """Walk lots in replay list order until cumulative positive unrealized gain reaches cap (FIFO consumption)."""
    if cap <= 0 or not gain_lots:
        return []
    picked: List[Dict[str, Any]] = []
    cum = 0.0
    for ln in gain_lots:
        g = max(0.0, _safe_float(ln.get("unrealized_pnl")))
        if g <= 0:
            continue
        picked.append(ln)
        cum += g
        if cum >= cap - 1e-6:
            break
    return picked


def _sort_suggested_by_security_by_estimated_net(
    by_sec: List[Dict[str, Any]],
    *,
    gain_tax_rate: float,
    trade_charges_pct: float,
    turnover_mult: float,
) -> List[Dict[str, Any]]:
    """
    Display order only: higher illustrative net (rate × Σ gain − blended cost on turnover_mult × Σ MV) first.
    Does not change FIFO bundle construction.
    """
    mult = max(0.0, turnover_mult) if turnover_mult > 0 else 2.0
    tcp = max(0.0, trade_charges_pct)
    r = max(0.0, gain_tax_rate)

    def net_for(group: Dict[str, Any]) -> float:
        lots = group.get("lots") or []
        tg = sum(max(0.0, _safe_float(x.get("gain"))) for x in lots)
        tmv = sum(max(0.0, _safe_float(x.get("market_value"))) for x in lots)
        eff = mult * tmv
        return tg * r - (tcp / 100.0) * eff

    return sorted(by_sec, key=net_for, reverse=True)


def _open_lot_passes_book_ltcg_strategy_gates(
    ln: Dict[str, Any],
    *,
    ltcg_rate: float,
    trade_charges_pct: float,
    turnover_mult: float,
    min_net_benefit_inr: float,
    min_tax_saved_to_trade_cost_ratio: float,
) -> bool:
    """
    True if a model sell + buy-back of the **full** open lot clears the same gates as strategy params
    (min_net_benefit_inr on net after cost, tax_saved ≥ ratio × trade_cost).
    """
    g = max(0.0, _safe_float(ln.get("unrealized_pnl")))
    if g <= 0:
        return False
    mv = max(0.0, _safe_float(ln.get("market_value")))
    mult = max(0.0, turnover_mult) if turnover_mult > 0 else 2.0
    tcp = max(0.0, trade_charges_pct)
    eff = mult * mv
    trade_cost = (tcp / 100.0) * eff
    tax_saved = g * max(0.0, ltcg_rate)
    net_benefit = tax_saved - trade_cost
    not_in_model = bool(ln.get("not_in_model")) and mv <= 1e-6
    ratio_ok = (
        tax_saved >= min_tax_saved_to_trade_cost_ratio * trade_cost - 1e-6 if trade_cost > 1e-6 else True
    )
    net_ok = net_benefit >= min_net_benefit_inr - 1e-6
    return bool(ratio_ok and (net_ok or not_in_model))


def _compute_book_ltcg_bundle(
    *,
    client_id: int,
    as_of: date,
    headroom: float,
    ltcg_rate: float,
    trade_charges_pct: float,
    p: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Returns (minimum_turnover_recommendation, rejection_reason, qualifying_ltcg_lots_for_ui).

    Third list: qualifying lots after strategy gates, **clubbed by security** (one row per ticker, multiple buy dates merged).
    FIFO subset toward headroom walks those clubbed rows in replay order.
    """
    min_net = _safe_float(p.get("min_net_benefit_inr", 5000))
    min_ratio = _safe_float(p.get("min_tax_saved_to_trade_cost_ratio", 2.0))
    mult = _safe_float(p.get("turnover_sell_buy_multiplier", 2.0))
    if mult <= 0:
        mult = 2.0

    open_lots = get_open_fifo_lots_with_mtm(client_id, as_of)
    ltcg_gain = [
        ln
        for ln in open_lots
        if ln.get("gain_type_if_sold") == "LTCG" and _safe_float(ln.get("unrealized_pnl")) > 0
    ]
    if not ltcg_gain:
        return None, {"message": "No LTCG-eligible lots with unrealized gain — nothing to book."}, []

    qualifying = [
        ln
        for ln in ltcg_gain
        if _open_lot_passes_book_ltcg_strategy_gates(
            ln,
            ltcg_rate=ltcg_rate,
            trade_charges_pct=trade_charges_pct,
            turnover_mult=mult,
            min_net_benefit_inr=min_net,
            min_tax_saved_to_trade_cost_ratio=min_ratio,
        )
    ]
    if not qualifying:
        return None, {
            "message": (
                "No LTCG lots pass strategy cost–benefit gates "
                f"(min net benefit ₹{min_net:,.0f} after trade cost; "
                f"estimated tax saved ≥ {min_ratio:g}× trade cost). "
                "Other lots may appear in FIFO replay but are hidden here."
            )
        }, []

    qualifying_clubbed = _club_ltcg_open_lots_by_security(qualifying)

    if headroom <= 0:
        return None, {"message": "No LTCG exemption headroom remaining this FY."}, qualifying_clubbed

    chosen = _fifo_gain_subset(qualifying_clubbed, headroom)
    if not chosen:
        return None, {"message": "Could not build a sell subset toward headroom."}, qualifying_clubbed

    total_gain = sum(max(0.0, _safe_float(x.get("unrealized_pnl"))) for x in chosen)
    booked = min(total_gain, headroom)
    total_turnover = sum(max(0.0, _safe_float(x.get("market_value"))) for x in chosen)
    effective_turnover = mult * total_turnover
    tcp = max(0.0, trade_charges_pct)
    trade_cost = (tcp / 100.0) * effective_turnover
    tax_saved = booked * ltcg_rate
    net_benefit = tax_saved - trade_cost
    all_no_price = all(_safe_float(x.get("mark_price")) <= 0 for x in chosen)
    not_in_model_exemption = all_no_price

    ratio_ok = tax_saved >= min_ratio * trade_cost - 1e-6 if trade_cost > 1e-6 else True
    net_ok = net_benefit >= min_net - 1e-6
    gates_passed = {
        "min_net_benefit": bool(net_ok or not_in_model_exemption),
        "tax_saved_vs_cost_ratio": bool(ratio_ok),
    }
    if not (gates_passed["min_net_benefit"] and gates_passed["tax_saved_vs_cost_ratio"]):
        rej = (
            f"Cost–benefit gates not met under conservative assumptions: "
            f"net ₹{net_benefit:,.0f} (min ₹{min_net:,.0f}), "
            f"tax saved ₹{tax_saved:,.0f} vs trade cost ₹{trade_cost:,.0f} "
            f"(need ratio ≥ {min_ratio})."
        )
        return None, {"message": rej}, qualifying_clubbed

    lot_rows = [_lot_template_row(x) for x in chosen]
    ratio_display = (tax_saved / trade_cost) if trade_cost > 1e-6 else None
    rec = {
        "message": (
            f"FIFO-style subset toward exemption headroom: book ≈₹{booked:,.0f} LTCG "
            f"(model sell + buy-back, {mult:g}× turnover @ {tcp:g}% blended cost)."
        ),
        "lots": lot_rows,
        "total_turnover": round(total_turnover, 2),
        "total_gain": round(total_gain, 2),
        "effective_turnover": round(effective_turnover, 2),
        "trade_cost": round(trade_cost, 2),
        "tax_saved": round(tax_saved, 2),
        "net_benefit": round(net_benefit, 2),
        "tax_saved_to_trade_cost_ratio": round(ratio_display, 4) if ratio_display is not None else None,
        "min_tax_saved_to_trade_cost_ratio_required": min_ratio,
        "not_in_model_exemption": not_in_model_exemption,
        "gates_passed": gates_passed,
        "buyback_delay_days": int(p.get("buyback_delay_days") or 2),
        "turnover_model": "in_model_2x",
    }
    return rec, None, qualifying_clubbed


def _compute_book_stcg_bundle(
    *,
    client_id: int,
    as_of: date,
    stcg_rate: float,
    trade_charges_pct: float,
    p: Dict[str, Any],
    fy_net_stcg_realized: float = 0.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    S2: crystallise unrealised STCG gains (model sell + buy-back).

    By default (**require_fy_stcg_loss_offset** true) this only clears when crystallising does **not**
    add FY STCG tax vs the client’s already-realized FY net STCG (i.e. short-term loss / set-off headroom
    in the model). That aligns S2 with “save current FY tax via set-off”; otherwise use **Book LTCG**
    for exemption headroom.

    Gates (when offset required): **min_net_benefit_inr** = gain − marginal STCG tax − trade cost;
    ratio compares **tax absorbed by offset** (naive tax on gain − marginal tax) vs trade cost.

    `max_stcg_crystallise_inr` 0 = unlimited cap (all filtered STCG gains in play).
    """
    min_net = _safe_float(p.get("min_net_benefit_inr", 5000))
    min_ratio = _safe_float(p.get("min_tax_saved_to_trade_cost_ratio", 2.0))
    mult = _safe_float(p.get("turnover_sell_buy_multiplier", 2.0))
    if mult <= 0:
        mult = 2.0
    max_book = _safe_float(p.get("max_stcg_crystallise_inr") or 0.0)
    require_offset = p.get("require_fy_stcg_loss_offset")
    if require_offset is None:
        require_offset = True
    max_marginal = _safe_float(p.get("max_allowed_incremental_stcg_tax_inr", 0.0))

    open_lots = get_open_fifo_lots_with_mtm(client_id, as_of)
    stcg_gain = [
        ln
        for ln in open_lots
        if ln.get("gain_type_if_sold") == "STCG" and _safe_float(ln.get("unrealized_pnl")) > 0
    ]
    stcg_gain = _filter_stcg_gain_lots_for_s2(stcg_gain, p)
    total_avail = sum(max(0.0, _safe_float(x.get("unrealized_pnl"))) for x in stcg_gain)
    cap = min(total_avail, max_book) if max_book > 0 else total_avail

    if not stcg_gain or cap <= 0:
        msg = (
            "No STCG open lots with unrealised gain after strategy filters, or crystallisation cap is zero."
        )
        return None, {"message": msg}, stcg_gain

    chosen = _fifo_gain_subset(stcg_gain, cap)
    if not chosen:
        return None, {"message": "Could not build a sell subset toward STCG crystallisation cap."}, stcg_gain

    total_gain = sum(max(0.0, _safe_float(x.get("unrealized_pnl"))) for x in chosen)
    booked = min(total_gain, cap)
    total_turnover = sum(max(0.0, _safe_float(x.get("market_value"))) for x in chosen)
    effective_turnover = mult * total_turnover
    tcp = max(0.0, trade_charges_pct)
    trade_cost = (tcp / 100.0) * effective_turnover
    all_no_price = all(_safe_float(x.get("mark_price")) <= 0 for x in chosen)
    not_in_model_exemption = all_no_price

    fy_net = float(fy_net_stcg_realized or 0.0)
    naive_tax_on_gain = booked * stcg_rate
    if bool(require_offset) and not not_in_model_exemption:
        incremental_tax = _marginal_stcg_tax_inr(fy_net, booked, stcg_rate)
        if incremental_tax > max_marginal + 1e-6:
            rej = (
                "Book STCG is suppressed: crystallising this gain would add estimated FY STCG tax "
                f"₹{incremental_tax:,.0f} (model marginal tax after FY net realized STCG ₹{fy_net:,.0f}). "
                "S2 is intended only when short-term loss / set-off headroom in the FY eliminates that "
                "extra tax — use harvest losses first, or Book LTCG for exemption headroom. "
                f"(Tolerance ₹{max_marginal:,.0f}; set require_fy_stcg_loss_offset=false to allow legacy behaviour.)"
            )
            return None, {"message": rej}, stcg_gain
        tax_shield_from_offset = max(0.0, naive_tax_on_gain - incremental_tax)
        spread_net = booked - incremental_tax - trade_cost
        ratio_ok = tax_shield_from_offset >= min_ratio * trade_cost - 1e-6 if trade_cost > 1e-6 else True
        spread_ok = spread_net >= min_net - 1e-6
        gates_passed = {
            "min_net_benefit": bool(spread_ok or not_in_model_exemption),
            "tax_saved_vs_cost_ratio": bool(ratio_ok or not_in_model_exemption),
        }
        if not (gates_passed["min_net_benefit"] and gates_passed["tax_saved_vs_cost_ratio"]):
            rej = (
                f"Cost–benefit gates not met for STCG crystallisation (after set-off model): "
                f"net after tax & cost ₹{spread_net:,.0f} (min ₹{min_net:,.0f}), "
                f"offset tax shield ₹{tax_shield_from_offset:,.0f} vs trade cost ₹{trade_cost:,.0f} "
                f"(need shield ≥ {min_ratio}× cost)."
            )
            return None, {"message": rej}, stcg_gain
        tax_saved_display = round(tax_shield_from_offset, 2)
        net_benefit_display = round(spread_net, 2)
    else:
        incremental_tax = naive_tax_on_gain
        spread_pre_tax = booked - trade_cost
        ratio_ok = booked >= min_ratio * trade_cost - 1e-6 if trade_cost > 1e-6 else True
        spread_ok = spread_pre_tax >= min_net - 1e-6
        gates_passed = {
            "min_net_benefit": bool(spread_ok or not_in_model_exemption),
            "tax_saved_vs_cost_ratio": bool(ratio_ok or not_in_model_exemption),
        }
        if not (gates_passed["min_net_benefit"] and gates_passed["tax_saved_vs_cost_ratio"]):
            rej = (
                f"Cost–benefit gates not met for STCG crystallisation: "
                f"pre-tax spread ₹{spread_pre_tax:,.0f} (min ₹{min_net:,.0f}), "
                f"gain ₹{booked:,.0f} vs trade cost ₹{trade_cost:,.0f} (need gain ≥ {min_ratio}× cost)."
            )
            return None, {"message": rej}, stcg_gain
        tax_saved_display = round(naive_tax_on_gain, 2)
        net_benefit_display = round(spread_pre_tax, 2)

    lot_rows = [_lot_template_row(x) for x in chosen]
    rec = {
        "strategy_kind": "book_stcg",
        "message": (
            f"FIFO-style subset toward STCG crystallisation ≈₹{booked:,.0f} "
            f"(model sell + buy-back, {mult:g}× turnover @ {tcp:g}% blended cost)."
        ),
        "lots": lot_rows,
        "total_turnover": round(total_turnover, 2),
        "total_gain": round(total_gain, 2),
        "effective_turnover": round(effective_turnover, 2),
        "trade_cost": round(trade_cost, 2),
        "incremental_stcg_tax_inr": round(incremental_tax, 2),
        "naive_stcg_tax_on_gain_inr": round(naive_tax_on_gain, 2),
        "fy_net_stcg_realized_inr": round(fy_net, 2),
        "tax_saved": tax_saved_display,
        "net_benefit": net_benefit_display,
        "not_in_model_exemption": not_in_model_exemption,
        "gates_passed": gates_passed,
        "buyback_delay_days": int(p.get("buyback_delay_days") or 2),
        "turnover_model": "in_model_2x",
        "require_fy_stcg_loss_offset": bool(require_offset),
    }
    return rec, None, stcg_gain


def _defer_lots(
    open_lots: List[Dict[str, Any]],
    *,
    max_days: int,
    min_unrealized: float,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ln in open_lots:
        if ln.get("gain_type_if_sold") != "STCG":
            continue
        dleft = int(ln.get("days_until_first_ltcg_sell_date") or 0)
        if dleft <= 0 or dleft > max_days:
            continue
        if _safe_float(ln.get("unrealized_pnl")) < min_unrealized:
            continue
        pd = ln["acquisition_date"]
        if isinstance(pd, str):
            pd = date.fromisoformat(pd[:10])
        out.append(
            {
                **ln,
                "acquisition_date": pd,
                "suggested_hold_message": (
                    f"≈{dleft} calendar day(s) until a sell can be LTCG under current rule; "
                    "basis and clock reset after any buy-back."
                ),
            }
        )
    out.sort(key=lambda x: int(x.get("days_until_first_ltcg_sell_date") or 9999))
    return out


def sell_timing_tax_advisory_for_security(
    client_id: int,
    security_id: int,
    *,
    as_of: Optional[date] = None,
    strategy_params_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[str]:
    """
    Unified recommendations: on SELL rows, hint when the sale may crystallise STCG and deferral toward
    LTCG may be worth discussing. Uses `defer_to_ltcg` params when that strategy row is still enabled;
    otherwise built-in defaults (same numbers as former loader defaults).
    """
    sp = strategy_params_map if strategy_params_map is not None else load_enabled_strategy_params()
    p = sp.get("defer_to_ltcg") or _DEFAULT_UNIFIED_SELL_TIMING_PARAMS
    max_days = int(p.get("max_days_until_ltcg_eligible") or 365)
    min_u = _safe_float(p.get("min_unrealized_gain_inr") or 0)
    d0 = as_of or date.today()
    try:
        open_lots = get_open_fifo_lots_with_mtm(int(client_id), d0)
    except Exception:
        return None
    sid = int(security_id)
    lots_for_sec = [ln for ln in open_lots if int(ln.get("security_id") or 0) == sid]
    if not lots_for_sec:
        return None
    candidates = _defer_lots(lots_for_sec, max_days=max_days, min_unrealized=min_u)
    if candidates:
        best = candidates[0]
        dleft = int(best.get("days_until_first_ltcg_sell_date") or 0)
        sym = (best.get("security_symbol") or "").strip()
        tail = f" ({sym})" if sym else ""
        return (
            f"Tax timing: if planning allows, waiting ≈{dleft} calendar day(s) may allow LTCG treatment "
            f"(often lower rate than STCG) under current rules{tail}. Illustrative only — confirm with CA."
        )
    for ln in lots_for_sec:
        if ln.get("gain_type_if_sold") != "STCG":
            continue
        if _safe_float(ln.get("unrealized_pnl")) <= 0:
            continue
        dleft = int(ln.get("days_until_first_ltcg_sell_date") or 0)
        sym = (ln.get("security_symbol") or "").strip()
        tail = f" ({sym})" if sym else ""
        if dleft > 0:
            return (
                "Tax: this sale may crystallise short-term capital gains (STCG). Consider deferring if prudent — "
                f"≈{dleft} calendar day(s) until a sale may qualify as LTCG under current rules{tail}. "
                "Illustrative only — confirm with CA."
            )
        return (
            "Tax: this sale may crystallise short-term capital gains (STCG). Consider deferring if timing allows "
            f"for LTCG eligibility{tail}. Illustrative only — confirm with CA."
        )
    return None


def defer_ltcg_advisory_text_for_security(
    client_id: int,
    security_id: int,
    *,
    as_of: Optional[date] = None,
    strategy_params_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[str]:
    """Backward-compatible alias for `sell_timing_tax_advisory_for_security`."""
    return sell_timing_tax_advisory_for_security(
        client_id, security_id, as_of=as_of, strategy_params_map=strategy_params_map
    )


def _harvest_unrealized_bundle(
    open_lots: List[Dict[str, Any]],
    *,
    stcg_rate: float,
    ltcg_rate: float,
    trade_charges_pct: float,
    p: Dict[str, Any],
    as_of: date,
    bonuses_by_security: Optional[Dict[str, Any]] = None,
    fy_realized_stcg_loss_inr: float = 0.0,
    fy_realized_ltcg_loss_inr: float = 0.0,
    fy_net_realized_stcg_inr: float = 0.0,
    fy_net_realized_ltcg_inr: float = 0.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Surfaces harvest only when there is FY **realised** gain of the **same character** to set off
    (positive net STCG for STCG-character unrealised losses; positive net LTCG for LTCG-character losses).
    Pure carry-forward (no current-year gain bucket) is not offered as this optimiser strategy.
    """
    min_net = _safe_float(p.get("min_net_benefit_inr", 5000))
    min_ratio = _safe_float(p.get("min_tax_saved_to_trade_cost_ratio", 2.0))
    mult = _safe_float(p.get("turnover_sell_buy_multiplier", 2.0))
    prioritize_948 = bool(p.get("prioritize_section_94_8_bonus_window", True))
    require_fy_gain = p.get("require_fy_realised_gain_for_setoff")
    if require_fy_gain is None:
        require_fy_gain = True
    loss_lots = [ln for ln in open_lots if _safe_float(ln.get("unrealized_pnl")) < 0]
    if not loss_lots:
        return None, {"message": "No open lots with unrealized loss."}

    fy_stcg = _safe_float(fy_net_realized_stcg_inr)
    fy_ltcg = _safe_float(fy_net_realized_ltcg_inr)
    if bool(require_fy_gain):
        eligible: List[Dict[str, Any]] = []
        for ln in loss_lots:
            gt = str(ln.get("gain_type_if_sold") or "STCG").upper()
            if gt == "LTCG":
                if fy_ltcg > 1e-6:
                    eligible.append(ln)
            else:
                if fy_stcg > 1e-6:
                    eligible.append(ln)
        if not eligible:
            return None, {
                "message": (
                    "Book unrealised losses is not offered as a strategy without FY realised capital gains "
                    "of the same character to set off: need positive FY net STCG for STCG-style open losses, "
                    "or positive FY net LTCG for LTCG-style open losses (report FIFO). "
                    "Pure carry-forward only is out of scope here — confirm with CA."
                )
            }
        loss_lots = eligible

    bonuses = bonuses_by_security or {}

    def _bonus_window_flag(ln: Dict[str, Any]) -> bool:
        if not prioritize_948 or not bonuses:
            return False
        return open_stcg_unrealized_loss_in_bonus_age_window(ln, as_of, bonuses, 9.0, 12.0)

    best_rec = None
    best_net = float("-inf")
    best_pri = False
    for ln in loss_lots:
        g = _safe_float(ln.get("unrealized_pnl"))
        loss = abs(g)
        gt = ln.get("gain_type_if_sold") or "STCG"
        rate = ltcg_rate if gt == "LTCG" else stcg_rate
        tax_shield = loss * rate
        mv = max(0.0, _safe_float(ln.get("market_value")))
        if mv <= 0:
            continue
        eff = mult * mv
        tcp = max(0.0, trade_charges_pct)
        trade_cost = (tcp / 100.0) * eff
        net = tax_shield - trade_cost
        ratio_ok = tax_shield >= min_ratio * trade_cost - 1e-6 if trade_cost > 1e-6 else True
        net_ok = net >= min_net - 1e-6
        if not (net_ok and ratio_ok):
            continue
        pri = _bonus_window_flag(ln)
        if net > best_net + 1e-6 or (abs(net - best_net) <= 1e-6 and pri and not best_pri):
            best_net = net
            best_pri = pri
            best_rec = {
                "message": (
                    "Book an unrealised loss (model sell + buy-back) to record STCL/LTCL for set-off and "
                    "carry-forward in subsequent years — confirm character and limits with CA."
                ),
                "lots": [_lot_template_row({**ln, "unrealized_pnl": g})],
                "total_turnover": round(mv, 2),
                "effective_turnover": round(eff, 2),
                "trade_cost": round(trade_cost, 2),
                "estimated_tax_shield_inr": round(tax_shield, 2),
                "net_benefit": round(net, 2),
                "gain_type": gt,
                "gates_passed": {"min_net_benefit": True, "tax_saved_vs_cost_ratio": True},
                "turnover_model": "in_model_2x",
                "prioritized_section_94_8_bonus_window": pri,
                "fy_realized_stcg_loss_inr": round(fy_realized_stcg_loss_inr, 2),
                "fy_realized_ltcg_loss_inr": round(fy_realized_ltcg_loss_inr, 2),
                "carryforward_note": (
                    "Model assumes current-year set-off against FY realised gains of the same character; "
                    "unused amounts may carry forward (STCL/LTCL rules). Illustrative only — not tax advice."
                ),
            }
    if not best_rec:
        return None, {
            "message": (
                f"No unrealised loss lot clears net ≥ ₹{min_net:,.0f} and "
                f"tax shield ≥ {min_ratio}× trade cost under blended cost assumptions."
            )
        }
    return best_rec, None


def enrich_client_suggestions(
    client: Dict[str, Any],
    *,
    fy_end: date,
    rates: Dict[str, Any],
    tax_settings: Dict[str, Any],
    strategy_params_map: Optional[Dict[str, Dict[str, Any]]] = None,
    bonuses_by_security: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Mutates client["suggestions"] in place: book_ltcg, book_stcg; harvest_unrealized_loss is always None (no trade suggestions).
    `defer_to_ltcg` is no longer a report strategy; timing hints attach to unified recommendation SELL rows.
    """
    sp = strategy_params_map if strategy_params_map is not None else load_enabled_strategy_params()
    cid = client.get("client_id")
    if cid is None:
        return

    ltcg_r = float(rates.get("ltcg_rate") or 0.0)
    stcg_r = float(rates.get("stcg_rate") or 0.0)
    tcp = float(tax_settings.get("trade_charges_pct") or 0.1)
    ltcg_mode = str(tax_settings.get("ltcg_eligibility_mode") or "twelve_months")

    sug = client.get("suggestions") or {}
    bl = sug.get("book_ltcg")
    headroom = _safe_float((bl or {}).get("headroom")) if bl else 0.0

    open_lots: List[Dict[str, Any]] = []
    try:
        open_lots = get_open_fifo_lots_with_mtm(int(cid), fy_end)
    except Exception:
        open_lots = []

    bonuses = bonuses_by_security if bonuses_by_security is not None else {}
    stcg_open_loss_mv = sum(
        abs(_safe_float(x.get("unrealized_pnl")))
        for x in open_lots
        if x.get("gain_type_if_sold") == "STCG" and _safe_float(x.get("unrealized_pnl")) < 0
    )
    fy_net_stcg_for_gate = _safe_float(client.get("realized_stcg"))
    has_stcg_loss_context_for_s2 = stcg_open_loss_mv > 1e-6 or fy_net_stcg_for_gate < -1e-6

    # --- S1 book LTCG ---
    if bl is not None and "book_ltcg_exemption" in sp:
        p_s1 = sp["book_ltcg_exemption"]
        rec, rej, ltcg_flat = _compute_book_ltcg_bundle(
            client_id=int(cid),
            as_of=fy_end,
            headroom=headroom,
            ltcg_rate=ltcg_r,
            trade_charges_pct=tcp,
            p=p_s1,
        )
        flat_for_ui = [_lot_template_row(x) for x in ltcg_flat]
        mult_s1 = _safe_float(p_s1.get("turnover_sell_buy_multiplier") or 2.0)
        by_sec = _sort_suggested_by_security_by_estimated_net(
            _group_by_security([_lot_template_row(x) for x in ltcg_flat]),
            gain_tax_rate=ltcg_r,
            trade_charges_pct=tcp,
            turnover_mult=mult_s1,
        )
        bl = dict(bl)
        bl["suggested_lots"] = flat_for_ui
        bl["suggested_by_security"] = by_sec
        bl["minimum_turnover_recommendation"] = rec
        bl["rejection_reason"] = rej
        bl["strategy_code"] = "book_ltcg_exemption"
        bl["strategy_label"] = "Book LTCG within annual exemption headroom"
        bl["applicable_rule_keys"] = [
            "default_ltcg_rate",
            "default_ltcg_exemption_limit",
            "ltcg_eligibility_mode",
            "trade_charges_pct",
            "book_ltcg_exemption.params",
        ]
        booked_gain = min(
            sum(_safe_float(x.get("gain")) for x in (rec.get("lots") or []) if rec),
            headroom,
        ) if rec else 0.0
        eff_t = _safe_float((rec or {}).get("effective_turnover"))
        trade_cost = _safe_float((rec or {}).get("trade_cost"))
        tax_saved = _safe_float((rec or {}).get("tax_saved"))
        net_b = _safe_float((rec or {}).get("net_benefit"))
        bl["benefit_breakdown"] = {
            "current_fy_tax_effect_inr": round(tax_saved, 2) if rec else 0.0,
            "future_tax_shield_inr_estimate": 0.0,
            "notes": [
                "Recommendations only clear when tax saved ≥ strategy min_tax_saved_to_trade_cost_ratio × trade cost (default 2×).",
                "Tax saved ≈ LTCG rate × booked gain (capped by headroom), illustrative.",
                "Sell + buy-back: blended % on 2× turnover; no ratio bypass on missing marks.",
            ],
        }
        bl["cost_breakdown"] = {
            "trade_charges_inr": round(trade_cost, 2),
            "turnover_inr": round(eff_t, 2),
            "other_inr": 0.0,
            "assumptions": [
                f"Blended transaction cost: {tcp}% on model turnover (conservative).",
                "2× turnover when modelling sell and repurchase both legs.",
                "Illustrative — not tax advice; confirm with CA.",
            ],
        }
        bl["net_metrics"] = {
            "net_current_fy_inr": round(net_b, 2) if rec else round(-trade_cost, 2) if rej else 0.0,
            "net_blended_inr_optional": round(net_b, 2) if rec else None,
        }
        bl["gates_passed"] = (rec or {}).get("gates_passed")
        bl["rules_snapshot"] = {
            "ltcg_rate": ltcg_r,
            "ltcg_exemption_limit": float(tax_settings.get("default_ltcg_exemption_limit") or 0),
            "ltcg_eligibility_mode": ltcg_mode,
            "trade_charges_pct": tcp,
            "strategy_params": p_s1,
        }
        # Badge only when there is an actionable minimum-turnover bundle (rec.lots).
        # qualifying_clubbed may be non-empty while rec is None (no headroom, subset fail, aggregate gates).
        has_qualified_ltcg = bool(rec and (rec.get("lots") or []))
        bl["report_book_ltcg_badge"] = bool(has_qualified_ltcg)
        bl["ltcg_no_qualified_trade_note"] = None
        if headroom > 1e-6 and not has_qualified_ltcg:
            bl["ltcg_no_qualified_trade_note"] = (
                "LTCG exemption headroom is available, but no open position clears strategy "
                "cost–benefit gates (minimum net benefit after trade cost; tax saved ≥ ratio × trade cost). "
                "Use FIFO replay or adjust strategy params — not tax advice."
            )
        sug["book_ltcg"] = bl
    elif bl is not None:
        sug["book_ltcg"] = bl

    # --- S2 book STCG (crystallise unrealised gains; only when ST loss headroom exists) ---
    if "book_stcg_gains" in sp:
        p_s2 = sp["book_stcg_gains"]
        fy_net_stcg = fy_net_stcg_for_gate
        if not has_stcg_loss_context_for_s2:
            sug["book_stcg"] = None
        else:
            s2_rec, s2_rej, stcg_flat = _compute_book_stcg_bundle(
                client_id=int(cid),
                as_of=fy_end,
                stcg_rate=stcg_r,
                trade_charges_pct=tcp,
                p=p_s2,
                fy_net_stcg_realized=fy_net_stcg,
            )
            flat_s2 = [_lot_template_row(x) for x in stcg_flat]
            mult_s2 = _safe_float(p_s2.get("turnover_sell_buy_multiplier") or 2.0)
            by_s2 = _sort_suggested_by_security_by_estimated_net(
                _group_by_security([_lot_template_row(x) for x in stcg_flat]),
                gain_tax_rate=stcg_r,
                trade_charges_pct=tcp,
                turnover_mult=mult_s2,
            )
            bs: Dict[str, Any] = {
                "message": (
                    "Secondary check (after ST loss headroom exists): crystallise unrealised STCG only when "
                    "marginal FY STCG tax stays within tolerance — set-off story. Prefer Book LTCG (S1) for exemption."
                ),
                "suggested_lots": flat_s2,
                "suggested_by_security": by_s2,
                "minimum_turnover_recommendation": s2_rec,
                "rejection_reason": s2_rej,
                "strategy_code": "book_stcg_gains",
                "strategy_label": "Book STCG (after ST losses; crystallise gains)",
                "applicable_rule_keys": [
                    "default_stcg_rate",
                    "ltcg_eligibility_mode",
                    "trade_charges_pct",
                    "book_stcg_gains.params",
                ],
            }
            if s2_rec:
                trade_cost = _safe_float(s2_rec.get("trade_cost"))
                inc_tax = _safe_float(s2_rec.get("incremental_stcg_tax_inr"))
                spread = _safe_float(s2_rec.get("net_benefit"))
                eff_t = _safe_float(s2_rec.get("effective_turnover"))
                bs["benefit_breakdown"] = {
                    "current_fy_tax_effect_inr": round(-inc_tax, 2),
                    "future_tax_shield_inr_estimate": 0.0,
                    "notes": [
                        "FY effect uses marginal STCG tax after FY net realized STCG (report FIFO), not naive rate × full gain.",
                        "When require_fy_stcg_loss_offset is on (default), S2 only appears if that marginal tax is ~0 — set-off story.",
                        "Net in payload = gain − marginal tax − trade cost (after-offset model).",
                    ],
                }
                bs["cost_breakdown"] = {
                    "trade_charges_inr": round(trade_cost, 2),
                    "turnover_inr": round(eff_t, 2),
                    "other_inr": 0.0,
                    "assumptions": [
                        f"Blended transaction cost: {tcp}% on model turnover (conservative).",
                        "2× turnover when modelling sell and repurchase both legs.",
                        "Illustrative — not tax advice; confirm with CA.",
                    ],
                }
                bs["net_metrics"] = {
                    "net_current_fy_inr": round(-inc_tax, 2),
                    "net_blended_inr_optional": round(spread, 2),
                }
                bs["gates_passed"] = s2_rec.get("gates_passed")
                bs["rules_snapshot"] = {
                    "stcg_rate": stcg_r,
                    "ltcg_eligibility_mode": ltcg_mode,
                    "trade_charges_pct": tcp,
                    "strategy_params": p_s2,
                }
            bs["report_book_stcg_badge"] = bool(s2_rec and (s2_rec.get("lots") or []))
            sug["book_stcg"] = bs
    else:
        sug["book_stcg"] = None

    sug["defer_to_ltcg"] = None

    # --- S3 unrealised loss booking (harvest_unrealized_loss): suppressed — no modeled trade suggestions ---
    sug["harvest_unrealized_loss"] = None

    u_ltcg_g = sum(
        _safe_float(x.get("unrealized_pnl"))
        for x in open_lots
        if x.get("gain_type_if_sold") == "LTCG" and _safe_float(x.get("unrealized_pnl")) > 0
    )
    u_ltcg_l = sum(
        abs(_safe_float(x.get("unrealized_pnl")))
        for x in open_lots
        if x.get("gain_type_if_sold") == "LTCG" and _safe_float(x.get("unrealized_pnl")) < 0
    )
    u_stcg_g = sum(
        _safe_float(x.get("unrealized_pnl"))
        for x in open_lots
        if x.get("gain_type_if_sold") == "STCG" and _safe_float(x.get("unrealized_pnl")) > 0
    )
    u_stcg_l = sum(
        abs(_safe_float(x.get("unrealized_pnl")))
        for x in open_lots
        if x.get("gain_type_if_sold") == "STCG" and _safe_float(x.get("unrealized_pnl")) < 0
    )
    client["unrealized_ltcg"] = round(u_ltcg_g, 2)
    client["unrealized_ltcg_loss"] = round(u_ltcg_l, 2)
    client["unrealized_stcg"] = round(u_stcg_g, 2)
    client["unrealized_stcg_loss"] = round(u_stcg_l, 2)

    if bonuses_by_security is not None:
        s94_meta = client_open_bonus_window_stcg_unrealized_loss_meta(open_lots, fy_end, bonuses_by_security)
    else:
        s94_meta = {"active": False, "security_ids": [], "sample_lots": [], "window_months": [9.0, 12.0]}
    client["section_94_8_open_bonus_window_loss"] = bool(s94_meta.get("active"))
    client["section_94_8_open_bonus_window_meta"] = s94_meta

    client["suggestions"] = sug


def attach_strategies_to_report(
    report: Dict[str, Any],
    *,
    fy_end: date,
    strategy_params_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    """Mutates report['clients'] in place."""
    rates = report.get("rates") or {}
    tax_settings = report.get("tax_settings") or {}
    spm = strategy_params_map if strategy_params_map is not None else load_enabled_strategy_params()
    try:
        bonuses_by_security = load_bonus_actions_by_security()
    except Exception:
        bonuses_by_security = {}
    for c in report.get("clients") or []:
        enrich_client_suggestions(
            c,
            fy_end=fy_end,
            rates=rates,
            tax_settings=tax_settings,
            strategy_params_map=spm,
            bonuses_by_security=bonuses_by_security,
        )
