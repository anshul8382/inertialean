"""
Normalize unified recommendation rows to executable trade semantics.

Action is derived from quantity change and amount only — never from UI badge text.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

_AMOUNT_RE = re.compile(r"Amount:\s*([-\d.]+)", re.I)
_CURRENT_QTY_RE = re.compile(r"Current Qty:\s*([-\d.]+)", re.I)
_NEW_QTY_RE = re.compile(r"New Qty:\s*([-\d.]+)", re.I)


def _to_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_amount_from_notes(notes: Optional[str]) -> float:
    if not notes:
        return 0.0
    m = _AMOUNT_RE.search(notes)
    return _to_float(m.group(1)) if m else 0.0


def parse_quantities_from_notes(notes: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    if not notes:
        return None, None
    cur = _CURRENT_QTY_RE.search(notes)
    new = _NEW_QTY_RE.search(notes)
    current_q = _to_float(cur.group(1)) if cur else None
    new_q = _to_float(new.group(1)) if new else None
    return current_q, new_q


def _trade_quantity_delta(
    *,
    quantity: Any,
    amount: float,
    target_price: float,
    current_quantity: Optional[float],
    new_quantity: Optional[float],
    notes: Optional[str],
) -> int:
    """Shares to buy/sell (signed): positive = buy, negative = sell."""
    cur_q = current_quantity
    new_q = new_quantity
    if notes and (cur_q is None or new_q is None):
        n_cur, n_new = parse_quantities_from_notes(notes)
        if cur_q is None and n_cur is not None:
            cur_q = n_cur
        if new_q is None and n_new is not None:
            new_q = n_new

    if cur_q is not None and new_q is not None:
        return int(round(new_q - cur_q))

    raw = int(round(_to_float(quantity)))
    if raw != 0:
        # UI often sends magnitude-only quantity; amount sign (or negative raw) indicates SELL.
        if amount < 0:
            return -abs(raw)
        if amount > 0:
            return abs(raw)
        return raw

    if amount != 0 and target_price > 0:
        signed = int(round(abs(amount) / target_price))
        return signed if amount > 0 else -signed

    return 0


def action_from_trade_quantity(trade_qty: int, amount: float = 0.0) -> str:
    """BUY / SELL / HOLD from signed trade quantity and optional amount."""
    if trade_qty > 0 or amount > 0:
        return "BUY"
    if trade_qty < 0 or amount < 0:
        return "SELL"
    return "HOLD"


def resolve_trade_action_and_quantity(
    *,
    quantity: Any = None,
    amount: Any = None,
    target_price: Any = None,
    current_quantity: Any = None,
    new_quantity: Any = None,
    notes: Optional[str] = None,
    action: Optional[str] = None,
) -> Tuple[str, int]:
    """
    Return (ACTION, trade_quantity) for persistence.
    trade_quantity is non-negative; direction is in ACTION.
    """
    amt = _to_float(amount)
    if amt == 0.0 and notes:
        amt = parse_amount_from_notes(notes)

    tp = _to_float(target_price)
    cur_q = _to_float(current_quantity) if current_quantity is not None else None
    new_q = _to_float(new_quantity) if new_quantity is not None else None

    signed_delta = _trade_quantity_delta(
        quantity=quantity,
        amount=amt,
        target_price=tp,
        current_quantity=cur_q,
        new_quantity=new_q,
        notes=notes,
    )

    explicit = (action or "").strip().upper()
    if explicit == "SELL" and signed_delta > 0 and amt <= 0:
        signed_delta = -abs(signed_delta)
    elif explicit == "BUY" and signed_delta < 0 and amt >= 0:
        signed_delta = abs(signed_delta)

    resolved = action_from_trade_quantity(signed_delta, amt)
    if explicit in ("BUY", "SELL") and resolved != explicit and signed_delta == 0 and amt == 0:
        resolved = explicit

    trade_qty = abs(signed_delta)
    if trade_qty <= 0 and resolved == "BUY" and amt > 0 and tp > 0:
        trade_qty = int(round(amt / tp))
    elif trade_qty <= 0 and resolved == "SELL" and amt < 0 and tp > 0:
        trade_qty = int(round(abs(amt) / tp))

    return resolved, trade_qty


def normalize_payload_rec(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a Section 1 record dict before DB save."""
    action, trade_qty = resolve_trade_action_and_quantity(
        quantity=rec.get("quantity"),
        amount=rec.get("amount"),
        target_price=rec.get("target_price") or rec.get("current_price"),
        current_quantity=rec.get("current_quantity"),
        new_quantity=rec.get("new_quantity"),
        notes=rec.get("notes"),
        action=rec.get("action"),
    )
    out = dict(rec)
    out["action"] = action.lower() if action != "HOLD" else "hold"
    out["quantity"] = trade_qty
    return out


def sync_recommendation_trade_action(trade) -> None:
    """Re-derive buy/sell action and magnitude quantity before recording or executing."""
    amount = parse_amount_from_notes(trade.notes)
    cur_q, new_q = parse_quantities_from_notes(trade.notes)
    action, trade_qty = resolve_trade_action_and_quantity(
        quantity=trade.quantity,
        amount=amount,
        target_price=trade.target_price,
        current_quantity=cur_q,
        new_quantity=new_q,
        notes=trade.notes,
        action=trade.action,
    )
    trade.action = action.lower() if action != "HOLD" else "hold"
    trade.quantity = trade_qty


def apply_section1_signed_display(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Section 1 UI uses signed trade quantity (negative = SELL) and signed amount.
    DB / API rows may store magnitude-only quantity with action — repair for display.
    """
    out = dict(rec)
    action = (rec.get("action") or "HOLD").strip().upper()
    price = _to_float(rec.get("current_price") or rec.get("target_price"))
    amount = _to_float(rec.get("amount"))

    cur_q = _to_float(rec.get("current_quantity")) if rec.get("current_quantity") is not None else None
    new_q = _to_float(rec.get("new_quantity")) if rec.get("new_quantity") is not None else None
    if cur_q is None or new_q is None:
        n_cur, n_new = parse_quantities_from_notes(
            rec.get("notes") if isinstance(rec.get("notes"), str) else None
        )
        if cur_q is None and n_cur is not None:
            cur_q = n_cur
        if new_q is None and n_new is not None:
            new_q = n_new

    signed_qty = 0
    if cur_q is not None and new_q is not None:
        signed_qty = int(round(new_q - cur_q))
        action = action_from_trade_quantity(signed_qty, amount)
    else:
        raw_q = int(round(_to_float(rec.get("quantity"))))
        if action == "SELL":
            signed_qty = -abs(raw_q) if raw_q else 0
        elif action == "BUY":
            signed_qty = abs(raw_q)
        elif raw_q < 0:
            signed_qty = raw_q
            action = "SELL"
        elif raw_q > 0:
            signed_qty = raw_q
            action = "BUY"

    if signed_qty == 0 and amount != 0 and price > 0:
        signed_qty = int(round(abs(amount) / price))
        if amount < 0:
            signed_qty = -signed_qty
        action = action_from_trade_quantity(signed_qty, amount)

    if signed_qty != 0 and price > 0:
        amount = float(signed_qty) * price

    out["action"] = action
    out["quantity"] = signed_qty
    out["amount"] = amount
    return out


def preview_recommendation_row_repair(recommendation) -> Tuple[bool, Dict[str, Any], Dict[str, Any]]:
    """
    Compute repair without mutating the row.
    Returns (would_change, before_snapshot, after_snapshot).
    """
    notes = recommendation.notes
    amount = parse_amount_from_notes(notes)
    cur_q, new_q = parse_quantities_from_notes(notes)

    new_action, trade_qty = resolve_trade_action_and_quantity(
        quantity=recommendation.quantity,
        amount=amount,
        target_price=recommendation.target_price,
        current_quantity=cur_q,
        new_quantity=new_q,
        notes=notes,
        action=recommendation.action,
    )

    before = {
        "action": (recommendation.action or "").strip().upper(),
        "quantity": int(round(_to_float(recommendation.quantity))),
    }
    after = {"action": new_action, "quantity": trade_qty}
    would_change = before["action"] != after["action"] or before["quantity"] != after["quantity"]
    return would_change, before, after


def repair_recommendation_row(recommendation) -> bool:
    """Fix action from notes amount and quantity fields. Returns True if updated."""
    would_change, _, after = preview_recommendation_row_repair(recommendation)
    if not would_change:
        return False
    recommendation.action = after["action"].lower() if after["action"] != "HOLD" else "hold"
    recommendation.quantity = after["quantity"]
    return True


def repair_client_recommendations(client_id: int, session=None) -> int:
    """Repair all recommendations for a client; returns count updated."""
    from models import Recommendation

    if session is None:
        from extensions import db

        session = db.session

    rows = Recommendation.query.filter_by(client_id=client_id).all()
    changed = 0
    for row in rows:
        if repair_recommendation_row(row):
            changed += 1
    if changed:
        session.commit()
    return changed
