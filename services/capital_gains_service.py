"""
Capital Gains Report Service

Reviews SELL transactions (excluding debt / fixed-income tax_rulesets) per financial year,
computes gain using FIFO, and classifies STCG vs LTCG using each security's AssetClass:
tax_ltcg_minimum_months (12 for listed equity, 24 for Gold / physical metal in Section 1 seed),
and optional tax_ltcg_eligibility_mode / tax_ltcg_holding_days overrides.

BONUS corporate actions (Section 55(2)(aa)): bonus shares are a separate FIFO lot at the
action date with nil cost per unit; original purchase lots are unchanged.
"""
from datetime import date, datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional
from decimal import Decimal
import math
import logging

from services.ltcg_eligibility import (
    LTCG_MODE_HOLDING_DAYS,
    classify_listed_equity_gain_type,
    first_day_after_n_calendar_months,
    ltcg_rule_public_label,
)

logger = logging.getLogger(__name__)

# Default FY: 1 Apr 2025 to 31 Mar 2026
FY_START_DEFAULT = date(2025, 4, 1)
FY_END_DEFAULT = date(2026, 3, 31)
LTCG_HOLDING_DAYS = 365

# Debt / fixed income: excluded from this equity-style STCG/LTCG report (see seed_section1_asset_class_tax_rules).
DEBT_RULESETS_EXCLUDED_FROM_CAPITAL_GAINS = frozenset(
    {
        "debt",
        "debt_mf",
        "debt_etf",
        "listed_bonds",
        "unlisted_bonds",
    }
)


def _looks_like_debt_etf_or_bond_misclassified(security) -> bool:
    """Heuristic when asset_class is wrong (e.g. Bharat Bond ETF on equity_etf)."""
    if not security:
        return False
    sym = (getattr(security, "symbol", None) or "").strip().upper()
    name = (getattr(security, "name", None) or "").strip().lower()
    if sym.startswith("EBBETF"):
        return True
    if "bharat bond" in name:
        return True
    return False


def _is_debt_tax_ruleset(security) -> bool:
    """
    True if this security must not use the listed-equity STCG/LTCG capital gains report:
    debt / fixed-income tax_ruleset, or known debt-ETF patterns when mis-tagged as equity.
    """
    if not security:
        return False
    ac = getattr(security, "asset_class", None)
    if ac:
        rs = (getattr(ac, "tax_ruleset", None) or "").strip().lower()
        if rs in DEBT_RULESETS_EXCLUDED_FROM_CAPITAL_GAINS:
            return True
    return _looks_like_debt_etf_or_bond_misclassified(security)


def is_excluded_debt_for_equity_capital_gains(security) -> bool:
    """True if security is omitted from STCG/LTCG report, pseudo trades, and related CSVs."""
    return _is_debt_tax_ruleset(security)


def _ltcg_classification_for_security(security, global_mode: str, global_min_days: int) -> Tuple[str, int, int]:
    """(mode, min_holding_days, minimum_ltcg_months) for classify_listed_equity_gain_type."""
    mode = (global_mode or "twelve_months").strip().lower()
    min_days = int(global_min_days)
    min_months = 12
    ac = getattr(security, "asset_class", None)
    if ac:
        min_months = int(getattr(ac, "tax_ltcg_minimum_months", None) or 12)
        ac_mode = (getattr(ac, "tax_ltcg_eligibility_mode", None) or "").strip().lower()
        if ac_mode:
            mode = ac_mode
        if mode == LTCG_MODE_HOLDING_DAYS:
            hd = getattr(ac, "tax_ltcg_holding_days", None)
            if hd is not None:
                min_days = int(hd)
    return mode, min_days, min_months


def _to_date(d) -> date:
    if d is None:
        return date.today()
    if isinstance(d, str):
        return datetime.strptime(d[:10], "%Y-%m-%d").date()
    if hasattr(d, "date"):
        return d.date()
    return d


def _decimal(val) -> Decimal:
    if val is None:
        return Decimal("0")
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val))


def get_capital_gains_report(
    fy_start: Optional[date] = None,
    fy_end: Optional[date] = None,
    client_id_filter: Optional[int] = None,
    client_name_filter: Optional[str] = None,
    ltcg_holding_days: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Build capital gains report for the given financial year.

    Returns:
        {
            "fy_start": date,
            "fy_end": date,
            "clients": [
                {
                    "client_id": int,
                    "client_name": str,
                    "sell_lines": [  # one per sell (or per lot line for detail)
                        {
                            "security_id", "security_name", "security_symbol",
                            "sell_date", "sell_txn_id", "quantity", "sell_price", "sale_value",
                            "cost_basis", "gain", "gain_type": "STCG" | "LTCG",
                            "purchase_date", "holding_days"
                        },
                        ...
                    ],
                    "total_stcg": Decimal,
                    "total_ltcg": Decimal,
                    "total_gain": Decimal,
                },
                ...
            ],
        }
    """
    from extensions import db
    from models import Transaction, CorporateAction, Client, Security
    from sqlalchemy.orm import joinedload

    fy_start = fy_start or FY_START_DEFAULT
    fy_end = fy_end or FY_END_DEFAULT

    hold_days = ltcg_holding_days
    if hold_days is None:
        try:
            from services.tax_optimiser_settings_service import get_ltcg_holding_days

            hold_days = get_ltcg_holding_days()
        except Exception:
            hold_days = LTCG_HOLDING_DAYS

    ltcg_mode = "twelve_months"
    ltcg_min_days = int(hold_days)
    try:
        from services.tax_optimiser_settings_service import get_ltcg_eligibility_settings

        ltcg_mode, ltcg_min_days = get_ltcg_eligibility_settings()
    except Exception:
        pass

    # Lots: list of (purchase_date, quantity, cost_per_unit)
    # We'll build events (date, type, payload) for each (client_id, security_id) that has a sell in FY
    # First get all sell txns in FY
    sell_q = (
        db.session.query(Transaction)
        .filter(
            Transaction.type == "SELL",
            Transaction.transaction_date >= datetime.combine(fy_start, datetime.min.time()),
            Transaction.transaction_date <= datetime.combine(fy_end, datetime.max.time()),
        )
    )
    if client_id_filter is not None:
        sell_q = sell_q.filter(Transaction.client_id == client_id_filter)
    sells = sell_q.order_by(Transaction.client_id, Transaction.security_id, Transaction.transaction_date).all()

    # Group sells by (client_id, security_id) so we can run FIFO once per group
    from collections import defaultdict
    by_client_security: Dict[Tuple[int, int], List[Transaction]] = defaultdict(list)
    for t in sells:
        by_client_security[(t.client_id, t.security_id)].append(t)

    clients_seen = set()
    result_clients: List[Dict[str, Any]] = []

    for (cid, sid), sell_list in sorted(by_client_security.items()):
        security = Security.query.options(joinedload(Security.asset_class)).get(sid)
        if not security:
            continue
        if _is_debt_tax_ruleset(security):
            continue

        # Optional: filter by client name
        if client_name_filter and client_name_filter.strip():
            client = Client.query.get(cid)
            if not client or client_name_filter.strip().lower() not in (client.name or "").lower():
                continue

        sec_mode, sec_min_days, sec_min_months = _ltcg_classification_for_security(
            security, ltcg_mode, ltcg_min_days
        )

        # Get all transactions for this client+security up to fy_end
        txns = (
            Transaction.query.filter(
                Transaction.client_id == cid,
                Transaction.security_id == sid,
                Transaction.transaction_date <= datetime.combine(fy_end, datetime.max.time()),
            )
            .order_by(Transaction.transaction_date)
            .all()
        )
        corporate_actions = (
            CorporateAction.query.filter(
                CorporateAction.security_id == sid,
                CorporateAction.action_date <= fy_end,
                CorporateAction.is_active == True,
                CorporateAction.action_type.in_(("SPLIT", "BONUS")),
            )
            .order_by(CorporateAction.action_date)
            .all()
        )

        # Build events (date, priority, type, payload)
        events: List[Tuple[date, int, str, Any]] = []
        for t in txns:
            d = _to_date(t.transaction_date)
            prio = 0 if (t.type == "SELL") else 1
            events.append((d, prio, "transaction", t))
        for ca in corporate_actions:
            d = _to_date(ca.action_date)
            events.append((d, 2, "corporate_action", ca))
        events.sort(key=lambda x: (x[0], x[1]))

        # FIFO lots: (purchase_date, quantity, cost_per_unit)
        lots: List[Tuple[date, float, Decimal]] = []
        client_stcg = Decimal("0")
        client_ltcg = Decimal("0")
        client_lines: List[Dict[str, Any]] = []

        security_name = security.name if security else ""
        security_symbol = security.symbol if security else ""

        for event_date, _prio, event_type, event in events:
            if event_type == "transaction":
                t = event
                if t.type == "BUY":
                    qty = float(t.quantity)
                    cost = _decimal(t.price)
                    lots.append((event_date, qty, cost))
                elif t.type == "SELL":
                    sell_date = event_date
                    to_sell = float(t.quantity)
                    sell_price = _decimal(t.price)
                    sale_value_total = sell_price * Decimal(str(to_sell))
                    # Always consume from lots (FIFO) for every SELL so lots state is correct for later sells.
                    # Only report (client_lines, client_stcg/ltcg) when sell is in current FY.
                    in_fy = fy_start <= sell_date <= fy_end

                    while to_sell > 0 and lots:
                        pd, q, cost_per_unit = lots[0]
                        consume = min(q, to_sell)
                        cost_basis = cost_per_unit * Decimal(str(consume))
                        sale_portion = sell_price * Decimal(str(consume))
                        gain = sale_portion - cost_basis
                        gain_type, holding_days = classify_listed_equity_gain_type(
                            pd,
                            sell_date,
                            mode=sec_mode,
                            min_holding_days=sec_min_days,
                            minimum_ltcg_months=sec_min_months,
                        )
                        if in_fy:
                            if gain_type == "STCG":
                                client_stcg += gain
                            else:
                                client_ltcg += gain
                            client_lines.append({
                                "security_id": sid,
                                "security_name": security_name,
                                "security_symbol": security_symbol,
                                "sell_date": sell_date,
                                "sell_txn_id": t.id,
                                "quantity": consume,
                                "sell_price": float(sell_price),
                                "sale_value": float(sale_portion),
                                "cost_basis": float(cost_basis),
                                "gain": float(gain),
                                "gain_type": gain_type,
                                "purchase_date": pd,
                                "holding_days": holding_days,
                            })

                        if q <= to_sell:
                            to_sell -= q
                            lots.pop(0)
                        else:
                            lots[0] = (pd, q - to_sell, cost_per_unit)
                            to_sell = 0

            elif event_type == "corporate_action":
                ca = event
                if ca.action_type == "SPLIT":
                    ratio = float(ca.ratio)
                    lots = [(pd, q * ratio, cost / Decimal(str(ratio))) for pd, q, cost in lots]
                elif ca.action_type == "BONUS":
                    # Section 55(2)(aa): cost of acquisition of bonus shares is NIL. Do not merge
                    # bonus qty into original lots at the same cost/unit (that overstates basis).
                    # FIFO: original lots unchanged; bonus is a new lot dated at corporate action.
                    total_qty = sum(q for _, q, _ in lots)
                    if total_qty > 0:
                        bonus_ratio = float(ca.ratio)
                        bonus_shares = math.floor(total_qty * bonus_ratio)
                        if bonus_shares > 0:
                            lots = list(lots)
                            lots.append((event_date, float(bonus_shares), Decimal("0")))

        if not client_lines:
            continue

        client = Client.query.get(cid)
        client_name = client.name if client else f"Client #{cid}"
        if cid not in clients_seen:
            clients_seen.add(cid)
            result_clients.append({
                "client_id": cid,
                "client_name": client_name,
                "sell_lines": client_lines,
                "total_stcg": client_stcg,
                "total_ltcg": client_ltcg,
                "total_gain": client_stcg + client_ltcg,
            })
        else:
            # Same client, another security — append to existing client
            for c in result_clients:
                if c["client_id"] == cid:
                    c["sell_lines"].extend(client_lines)
                    c["total_stcg"] += client_stcg
                    c["total_ltcg"] += client_ltcg
                    c["total_gain"] += client_stcg + client_ltcg
                    break

    # Sort sell_lines by sell_date within each client
    for c in result_clients:
        c["sell_lines"].sort(key=lambda r: (r["sell_date"], r["security_name"], r["purchase_date"]))

    return {
        "fy_start": fy_start,
        "fy_end": fy_end,
        "clients": result_clients,
        "ltcg_eligibility_mode": ltcg_mode,
        "ltcg_rule_label": ltcg_rule_public_label(ltcg_mode),
        "ltcg_min_holding_days": ltcg_min_days,
        "capital_gains_note": (
            "Debt / fixed-income rulesets (debt, debt_mf, debt_etf, listed_bonds, unlisted_bonds) are excluded; "
            "Bharat Bond–style symbols (e.g. EBBETF*) are excluded even if mis-tagged as equity. "
            "STCG/LTCG for other assets uses each asset class tax_ltcg_minimum_months "
            "(e.g. Gold = 24 months per Section 1 seed / IT rules for physical metal)."
        ),
    }


def get_client_fy_realised_gains_before_date(
    client_id: int,
    fy_start: date,
    fy_end: date,
    before_date: date,
) -> Dict[str, Any]:
    """
    Sum realised STCG/LTCG for one client in [fy_start, fy_end] from recorded SELLs
    whose sell date is strictly before before_date (same rules as get_capital_gains_report).
    """
    report = get_capital_gains_report(
        fy_start=fy_start,
        fy_end=fy_end,
        client_id_filter=client_id,
    )
    stcg = 0.0
    ltcg = 0.0
    for c in report.get("clients") or []:
        if c["client_id"] != client_id:
            continue
        for line in c["sell_lines"]:
            sd = _to_date(line["sell_date"])
            if sd >= before_date:
                continue
            g = float(line["gain"])
            if line["gain_type"] == "STCG":
                stcg += g
            else:
                ltcg += g
        break
    return {
        "total_stcg": stcg,
        "total_ltcg": ltcg,
        "total_gain": stcg + ltcg,
    }


def _format_lots_for_audit(lots: List[Tuple[date, float, Decimal]]) -> str:
    if not lots:
        return "No open lots."
    parts: List[str] = []
    for i, (pd, q, c) in enumerate(lots, 1):
        emb = float(Decimal(str(q)) * c)
        cz = float(c)
        parts.append(
            f"#{i} acq {pd.isoformat()} · qty {q:g} · ₹/unit {cz:.4f} · embedded ₹{emb:.2f}"
        )
    return "\n".join(parts)


def _lots_to_serializable(lots: List[Tuple[date, float, Decimal]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for pd, q, c in lots:
        out.append(
            {
                "acquisition_date": pd.isoformat(),
                "quantity": float(q),
                "cost_per_unit": float(c),
                "embedded_cost": float(Decimal(str(q)) * c),
            }
        )
    return out


def _run_fifo_replay(
    client_id: int,
    security_id: int,
    fy_end: date,
    *,
    stop_after_sell_txn_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Single implementation of FIFO replay used by get_capital_gains_report logic
    (BUY/SELL + SPLIT/BONUS ordering and formulas).

    If stop_after_sell_txn_id is set, stop immediately after processing that SELL txn (inclusive).
    If None, run through all events (full replay).
    """
    from models import Transaction, CorporateAction, Security, Client
    from sqlalchemy.orm import joinedload

    fy_end_dt = datetime.combine(fy_end, datetime.max.time())

    ltcg_mode = "twelve_months"
    ltcg_min_days = LTCG_HOLDING_DAYS
    try:
        from services.tax_optimiser_settings_service import get_ltcg_eligibility_settings

        ltcg_mode, ltcg_min_days = get_ltcg_eligibility_settings()
    except Exception:
        try:
            from services.tax_optimiser_settings_service import get_ltcg_holding_days

            ltcg_min_days = get_ltcg_holding_days()
        except Exception:
            pass

    sec = Security.query.options(joinedload(Security.asset_class)).get(security_id)
    sec_mode, sec_min_days, sec_min_months = _ltcg_classification_for_security(
        sec, ltcg_mode, ltcg_min_days
    )

    txns = (
        Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.security_id == security_id,
            Transaction.transaction_date <= fy_end_dt,
        )
        .order_by(Transaction.transaction_date)
        .all()
    )
    corporate_actions = (
        CorporateAction.query.filter(
            CorporateAction.security_id == security_id,
            CorporateAction.action_date <= fy_end_dt,
            CorporateAction.is_active == True,
            CorporateAction.action_type.in_(("SPLIT", "BONUS")),
        )
        .order_by(CorporateAction.action_date)
        .all()
    )

    events: List[Tuple[date, int, str, Any]] = []
    for t in txns:
        d = _to_date(t.transaction_date)
        prio = 0 if (t.type == "SELL") else 1
        events.append((d, prio, "transaction", t))
    for ca in corporate_actions:
        d = _to_date(ca.action_date)
        events.append((d, 2, "corporate_action", ca))
    events.sort(key=lambda x: (x[0], x[1]))

    lots: List[Tuple[date, float, Decimal]] = []
    steps: List[Dict[str, Any]] = []
    sym = (sec.symbol or sec.name or "") if sec else ""
    cli = Client.query.get(client_id)
    cname = cli.name if cli else f"Client #{client_id}"

    for event_date, _prio, event_type, event in events:
        if event_type == "transaction":
            t = event
            if t.type == "BUY":
                qty = float(t.quantity)
                cost = _decimal(t.price)
                lots.append((event_date, qty, cost))
                steps.append(
                    {
                        "kind": "buy",
                        "txn_id": t.id,
                        "date": event_date.isoformat(),
                        "summary": f"BUY txn #{t.id}: +{qty:g} @ ₹{float(cost):.4f}/unit",
                        "note": "Opens a new FIFO lot (acquisition date = trade date).",
                        "lots_after": _format_lots_for_audit(lots),
                    }
                )
            elif t.type == "SELL":
                sell_date = event_date
                to_sell = float(t.quantity)
                sell_price = _decimal(t.price)
                match_stop = (
                    stop_after_sell_txn_id is not None
                    and int(t.id) == int(stop_after_sell_txn_id)
                )
                slices: List[Dict[str, Any]] = []
                slice_idx = 0
                while to_sell > 0 and lots:
                    pd, q, cost_per_unit = lots[0]
                    consume = min(q, to_sell)
                    cost_basis = cost_per_unit * Decimal(str(consume))
                    sale_portion = sell_price * Decimal(str(consume))
                    gain = sale_portion - cost_basis
                    gain_type, holding_days = classify_listed_equity_gain_type(
                        pd,
                        sell_date,
                        mode=sec_mode,
                        min_holding_days=sec_min_days,
                        minimum_ltcg_months=sec_min_months,
                    )
                    slice_idx += 1
                    slices.append(
                        {
                            "slice": slice_idx,
                            "matched_acquisition_date": pd.isoformat(),
                            "quantity": consume,
                            "cost_per_unit": float(cost_per_unit),
                            "cost_basis": float(cost_basis),
                            "sale_value": float(sale_portion),
                            "gain": float(gain),
                            "gain_type": gain_type,
                            "holding_days": holding_days,
                            "minimum_ltcg_months": sec_min_months,
                        }
                    )
                    if q <= to_sell:
                        to_sell -= q
                        lots.pop(0)
                    else:
                        lots[0] = (pd, q - to_sell, cost_per_unit)
                        to_sell = 0

                steps.append(
                    {
                        "kind": "sell",
                        "txn_id": t.id,
                        "date": sell_date.isoformat(),
                        "is_stop_target": bool(match_stop),
                        "summary": (
                            f"SELL txn #{t.id}: {float(t.quantity):g} @ ₹{float(sell_price):.4f}/unit — "
                            f"{len(slices)} FIFO slice(s)"
                        ),
                        "fifo_slices": slices,
                        "lots_after": _format_lots_for_audit(lots),
                    }
                )
                if match_stop:
                    return {
                        "ok": True,
                        "stopped_early": True,
                        "client_id": client_id,
                        "client_name": cname,
                        "security_id": security_id,
                        "security_symbol": sym,
                        "fy_end": fy_end.isoformat(),
                        "ltcg_eligibility_mode": ltcg_mode,
                        "ltcg_rule_label": ltcg_rule_public_label(ltcg_mode),
                        "ltcg_min_holding_days": ltcg_min_days,
                        "steps": steps,
                        "lots_remaining": _lots_to_serializable(lots),
                    }

        elif event_type == "corporate_action":
            ca = event
            if ca.action_type == "SPLIT":
                ratio = float(ca.ratio)
                if ratio <= 0:
                    steps.append(
                        {
                            "kind": "split",
                            "date": event_date.isoformat(),
                            "summary": f"SPLIT ignored (non-positive ratio={ratio})",
                            "lots_after": _format_lots_for_audit(lots),
                        }
                    )
                else:
                    lots = [(pd, q * ratio, cost / Decimal(str(ratio))) for pd, q, cost in lots]
                    steps.append(
                        {
                            "kind": "split",
                            "date": event_date.isoformat(),
                            "summary": (
                                f"SPLIT ratio {ratio:g}:1 — multiply each lot qty by {ratio:g}, "
                                f"divide cost/unit by {ratio:g} (total cost unchanged)."
                            ),
                            "lots_after": _format_lots_for_audit(lots),
                        }
                    )
            elif ca.action_type == "BONUS":
                total_qty = sum(q for _, q, _ in lots)
                if total_qty > 0:
                    bonus_ratio = float(ca.ratio)
                    bonus_shares = math.floor(total_qty * bonus_ratio)
                    if bonus_shares > 0:
                        lots = list(lots)
                        lots.append((event_date, float(bonus_shares), Decimal("0")))
                    steps.append(
                        {
                            "kind": "bonus",
                            "date": event_date.isoformat(),
                            "summary": (
                                f"BONUS ratio {bonus_ratio:g}: +{bonus_shares:g} shares as new lot on "
                                f"{event_date.isoformat()} at ₹0/unit (Section 55(2)(aa))."
                            ),
                            "lots_after": _format_lots_for_audit(lots),
                        }
                    )
                else:
                    steps.append(
                        {
                            "kind": "bonus",
                            "date": event_date.isoformat(),
                            "summary": "BONUS skipped (no open lots).",
                            "lots_after": _format_lots_for_audit(lots),
                        }
                    )

    base = {
        "ok": True,
        "stopped_early": False,
        "client_id": client_id,
        "client_name": cname,
        "security_id": security_id,
        "security_symbol": sym,
        "fy_end": fy_end.isoformat(),
        "ltcg_eligibility_mode": ltcg_mode,
        "ltcg_rule_label": ltcg_rule_public_label(ltcg_mode),
        "ltcg_min_holding_days": ltcg_min_days,
        "steps": steps,
        "lots_remaining": _lots_to_serializable(lots),
    }
    if stop_after_sell_txn_id is not None:
        return {
            "ok": False,
            "error": (
                f"Sell transaction id {stop_after_sell_txn_id} not found for client {client_id}, "
                f"security {security_id} on or before {fy_end.isoformat()}."
            ),
            "steps": steps,
            "lots_remaining": _lots_to_serializable(lots),
        }
    return base


def compute_pseudo_sell_capital_gains(
    client_id: int,
    security_id: int,
    sell_date: date,
    quantity: float,
    sell_price_per_unit,
) -> Dict[str, Any]:
    """
    Replay FIFO through sell_date, then apply a hypothetical SELL in memory (no DB writes).
    Same FIFO slice / STCG–LTCG classification as realised sells in _run_fifo_replay.
    """
    from models import Client, Security
    from sqlalchemy.orm import joinedload

    if quantity is None or float(quantity) <= 0:
        return {"ok": False, "error": "Quantity must be positive"}

    sec = Security.query.options(joinedload(Security.asset_class)).get(security_id)
    if not sec:
        return {"ok": False, "error": "Security not found"}
    if _is_debt_tax_ruleset(sec):
        return {
            "ok": False,
            "error": (
                "Debt / fixed-income securities (tax rulesets debt, debt_mf, debt_etf, listed_bonds, unlisted_bonds; "
                "and Bharat Bond–style tickers) are excluded from this STCG/LTCG preview — same as the capital gains report."
            ),
        }

    replay = _run_fifo_replay(client_id, security_id, sell_date, stop_after_sell_txn_id=None)
    if not replay.get("ok"):
        return {"ok": False, "error": replay.get("error") or "FIFO replay failed"}

    lots_raw = replay.get("lots_remaining") or []
    lots: List[Tuple[date, float, Decimal]] = []
    for d in lots_raw:
        lots.append((_to_date(d["acquisition_date"]), float(d["quantity"]), _decimal(d["cost_per_unit"])))

    available = sum(q for _pd, q, _c in lots)
    qty = float(quantity)
    if qty > available + 1e-9:
        return {
            "ok": False,
            "error": (
                f"Cannot sell {qty:g} units; only {available:g} on hand as of {sell_date.isoformat()} "
                "(per FIFO replay through that date)."
            ),
            "available_quantity": available,
        }

    ltcg_mode = replay.get("ltcg_eligibility_mode") or "twelve_months"
    ltcg_min_days = int(replay.get("ltcg_min_holding_days") or LTCG_HOLDING_DAYS)
    sec_mode, sec_min_days, sec_min_months = _ltcg_classification_for_security(
        sec, ltcg_mode, ltcg_min_days
    )

    sell_price = _decimal(sell_price_per_unit)
    to_sell = qty
    slices: List[Dict[str, Any]] = []
    stcg = Decimal("0")
    ltcg = Decimal("0")
    stcg_qty = 0.0
    stcg_cost = 0.0
    stcg_sale = 0.0
    ltcg_qty = 0.0
    ltcg_cost = 0.0
    ltcg_sale = 0.0
    slice_idx = 0
    while to_sell > 0 and lots:
        pd, q, cost_per_unit = lots[0]
        consume = min(q, to_sell)
        cost_basis = cost_per_unit * Decimal(str(consume))
        sale_portion = sell_price * Decimal(str(consume))
        gain = sale_portion - cost_basis
        gain_type, holding_days = classify_listed_equity_gain_type(
            pd,
            sell_date,
            mode=sec_mode,
            min_holding_days=sec_min_days,
            minimum_ltcg_months=sec_min_months,
        )
        slice_idx += 1
        slices.append(
            {
                "slice": slice_idx,
                "matched_acquisition_date": pd.isoformat(),
                "quantity": consume,
                "cost_per_unit": float(cost_per_unit),
                "cost_basis": float(cost_basis),
                "sale_value": float(sale_portion),
                "gain": float(gain),
                "gain_type": gain_type,
                "holding_days": holding_days,
            }
        )
        if gain_type == "STCG":
            stcg += gain
            stcg_qty += consume
            stcg_cost += float(cost_basis)
            stcg_sale += float(sale_portion)
        else:
            ltcg += gain
            ltcg_qty += consume
            ltcg_cost += float(cost_basis)
            ltcg_sale += float(sale_portion)
        if q <= to_sell:
            to_sell -= q
            lots.pop(0)
        else:
            lots[0] = (pd, q - to_sell, cost_per_unit)
            to_sell = 0

    cli = Client.query.get(client_id)
    sale_total = sell_price * Decimal(str(qty))

    from services.fy_tax_utils import fy_for_date, fy_label

    fy_s, fy_e = fy_for_date(sell_date)
    prior_fy = get_client_fy_realised_gains_before_date(client_id, fy_s, fy_e, sell_date)
    b_st = float(prior_fy["total_stcg"])
    b_lt = float(prior_fy["total_ltcg"])
    h_st = float(stcg)
    h_lt = float(ltcg)

    return {
        "ok": True,
        "client_id": client_id,
        "client_name": cli.name if cli else str(client_id),
        "security_id": security_id,
        "security_symbol": replay.get("security_symbol") or "",
        "sell_date": sell_date.isoformat(),
        "quantity": qty,
        "sell_price_per_unit": float(sell_price),
        "sale_value": float(sale_total),
        "total_stcg": float(stcg),
        "total_ltcg": float(ltcg),
        "total_gain": float(stcg + ltcg),
        "stcg_totals": {
            "quantity": stcg_qty,
            "cost_basis": stcg_cost,
            "sale_value": stcg_sale,
            "gain": float(stcg),
        },
        "ltcg_totals": {
            "quantity": ltcg_qty,
            "cost_basis": ltcg_cost,
            "sale_value": ltcg_sale,
            "gain": float(ltcg),
        },
        "fifo_slices": slices,
        "ltcg_rule_label": replay.get("ltcg_rule_label"),
        "disclaimer": "Hypothetical only; same FIFO rules as the capital gains report. Not tax advice.",
        "fy_before_after": {
            "fy_label": fy_label(fy_s),
            "fy_start": fy_s.isoformat(),
            "fy_end": fy_e.isoformat(),
            "before_stcg": b_st,
            "before_ltcg": b_lt,
            "before_total_gain": b_st + b_lt,
            "hypo_stcg": h_st,
            "hypo_ltcg": h_lt,
            "hypo_total_gain": h_st + h_lt,
            "after_stcg": b_st + h_st,
            "after_ltcg": b_lt + h_lt,
            "after_total_gain": b_st + b_lt + h_st + h_lt,
            "note": (
                "FY is the Indian financial year that contains the hypothetical sell date. "
                "Before = realised STCG/LTCG in that FY from recorded sells with sell date strictly before "
                "this hypothetical trade. After = before + this hypothetical trade only (same-day recorded "
                "sells are not included in Before)."
            ),
        },
    }


def get_capital_gains_fifo_full_replay(
    client_id: int,
    security_id: int,
    fy_end: date,
) -> Dict[str, Any]:
    """Full FIFO replay through fy_end — same engine as get_capital_gains_report (for CLI / verification)."""
    out = _run_fifo_replay(client_id, security_id, fy_end, stop_after_sell_txn_id=None)
    out["disclaimer"] = (
        "Same FIFO rules as get_capital_gains_report: SPLIT qty×ratio & cost/ratio; "
        "BONUS nil-cost lot (§55(2)(aa)). Not tax advice."
    )
    return out


def get_capital_gains_fifo_audit_trail(
    client_id: int,
    security_id: int,
    sell_txn_id: int,
    fy_end: date,
) -> Dict[str, Any]:
    """
    Replay up to and including one SELL txn (same rules as get_capital_gains_report).
    """
    out = _run_fifo_replay(client_id, security_id, fy_end, stop_after_sell_txn_id=int(sell_txn_id))
    if out.get("ok") and out.get("stopped_early"):
        out["sell_txn_id"] = int(sell_txn_id)
        out["disclaimer"] = (
            "Replay uses the same FIFO rules as the capital gains report: "
            "SPLIT scales qty and divides cost/unit; BONUS adds a nil-cost lot (§55(2)(aa)). "
            "Not tax advice."
        )
        return out
    return out


def list_client_security_ids_with_activity(client_id: int, as_of: date) -> List[int]:
    """Distinct securities this client has traded through as_of (for open-lot replay)."""
    from extensions import db
    from models import Transaction
    from sqlalchemy import distinct

    rows = (
        db.session.query(distinct(Transaction.security_id))
        .filter(
            Transaction.client_id == client_id,
            Transaction.transaction_date <= datetime.combine(as_of, datetime.max.time()),
        )
        .all()
    )
    return [int(r[0]) for r in rows]


def get_open_fifo_lots_with_mtm(client_id: int, as_of: date) -> List[Dict[str, Any]]:
    """
    Open FIFO lots at `as_of` with mark-to-market using Security.current_price.

    Each row: security_id, security_symbol, security_name, acquisition_date (date),
    quantity, cost_per_unit, cost_basis, mark_price, not_in_model, market_value,
    unrealized_pnl, gain_type_if_sold, holding_days, days_until_first_ltcg_sell_date (0 if already LTCG).
    """
    from models import Security
    from sqlalchemy.orm import joinedload
    from services.ltcg_eligibility import (
        LTCG_MODE_HOLDING_DAYS,
        classify_listed_equity_gain_type,
    )

    ltcg_mode = "twelve_months"
    ltcg_min_days = LTCG_HOLDING_DAYS
    try:
        from services.tax_optimiser_settings_service import get_ltcg_eligibility_settings

        ltcg_mode, ltcg_min_days = get_ltcg_eligibility_settings()
    except Exception:
        pass

    out: List[Dict[str, Any]] = []
    for sid in list_client_security_ids_with_activity(client_id, as_of):
        rep = _run_fifo_replay(client_id, sid, as_of, stop_after_sell_txn_id=None)
        if not rep.get("ok"):
            continue
        sec = Security.query.options(joinedload(Security.asset_class)).get(sid)
        sym = (sec.symbol or "") if sec else ""
        name = (sec.name or sym) if sec else ""
        mark = float(sec.current_price or 0) if sec else 0.0
        not_in_model = mark <= 0
        sec_mode, sec_min_days, sec_min_months = _ltcg_classification_for_security(
            sec, ltcg_mode, ltcg_min_days
        )

        for lot in rep.get("lots_remaining") or []:
            pd = _to_date(lot["acquisition_date"])
            qty = float(lot["quantity"])
            cpu = float(lot["cost_per_unit"])
            embedded = float(lot["embedded_cost"])
            mv = qty * mark if mark > 0 else 0.0
            pnl = mv - embedded
            gt, hd = classify_listed_equity_gain_type(
                pd,
                as_of,
                mode=sec_mode,
                min_holding_days=int(sec_min_days),
                minimum_ltcg_months=sec_min_months,
            )
            if (sec_mode or "").strip().lower() == LTCG_MODE_HOLDING_DAYS:
                first_ltcg_day = pd + timedelta(days=max(1, int(sec_min_days)))
            else:
                first_ltcg_day = first_day_after_n_calendar_months(pd, sec_min_months) + timedelta(days=1)
            days_to = max(0, (first_ltcg_day - as_of).days) if gt == "STCG" else 0
            out.append(
                {
                    "security_id": sid,
                    "security_symbol": sym,
                    "security_name": name,
                    "acquisition_date": pd,
                    "quantity": qty,
                    "cost_per_unit": cpu,
                    "cost_basis": embedded,
                    "mark_price": mark,
                    "not_in_model": not_in_model,
                    "market_value": round(mv, 2),
                    "unrealized_pnl": round(pnl, 2),
                    "gain_type_if_sold": gt,
                    "holding_days": hd,
                    "days_until_first_ltcg_sell_date": days_to,
                }
            )
    return out
