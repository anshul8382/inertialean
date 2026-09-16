"""Find BUY/SELL trades that have no matching cashflow row.

Cashflow has no transaction_id. A trade is covered when:
  1) a same-day cashflow matches the auto-generated description, or
  2) a cashflow (same day, then ±3 settlement days) matches the expected
     signed amount within the amount band, or
  3) leftover day-net equals leftover cashflow net (bulk day total).

INFLOW with either stored sign is treated as an investment (negative).
Read-only. Dummy opening-book dates are skipped.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from services.cashflow_service import (
    DUMMY_DATES as _DUMMY_DATE_LIST,
    MATCH_NEARBY_FLOOR,
    MATCH_NEARBY_PCT,
    MATCH_SAME_DAY_FLOOR,
    MATCH_SAME_DAY_PCT,
    MATCH_SETTLEMENT_DAYS,
    canonical_cashflow_signed_amount,
)

AMOUNT_TOLERANCE = Decimal("1.00")  # legacy exact-line floor; bands used below
DUMMY_DATES = set(_DUMMY_DATE_LIST)
SETTLEMENT_DAYS = MATCH_SETTLEMENT_DAYS
LINE_AMOUNT_CAP = Decimal("5000")
LINE_AMOUNT_FLOOR = Decimal("100")


def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    try:
        return value.date()
    except Exception:
        return None


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _qty_disp(quantity) -> Any:
    qty = _dec(quantity)
    try:
        if qty == qty.to_integral_value():
            return int(qty)
    except Exception:
        pass
    return quantity


def expected_signed_amount(txn_type: str, amount, quantity=None, price=None) -> Decimal:
    raw = _dec(amount)
    if raw == 0 and quantity is not None and price is not None:
        raw = _dec(quantity) * _dec(price)
    ttype = (txn_type or "").upper()
    if ttype == "BUY":
        return -raw
    if ttype == "SELL":
        return raw
    return Decimal("0")


def auto_descriptions(txn_type: str, quantity, symbol: str) -> List[str]:
    ttype = (txn_type or "").upper()
    qty_disp = _qty_disp(quantity)
    symbol = symbol or "UNKNOWN"
    return [
        f"{ttype} {qty_disp} shares of {symbol}",
        f"{ttype} {quantity} shares of {symbol}",
        f"{ttype} {qty_disp} shares",
        f"{ttype} {quantity} shares",
    ]


def _cf_signed(cf: Dict[str, Any]) -> Decimal:
    return Decimal(str(canonical_cashflow_signed_amount(cf)))


def _line_amount_tol(expected: Decimal) -> Decimal:
    """Small slack for charges/rounding; cap so large trades are not glued to the wrong CF."""
    pct = abs(expected) * Decimal(str(MATCH_SAME_DAY_PCT))
    return max(LINE_AMOUNT_FLOOR, min(LINE_AMOUNT_CAP, pct if pct > 0 else LINE_AMOUNT_FLOOR))


def _day_net_tol(a: Decimal, b: Decimal, *, nearby: bool = False) -> Decimal:
    floor = Decimal(str(MATCH_NEARBY_FLOOR if nearby else MATCH_SAME_DAY_FLOOR))
    pct = Decimal(str(MATCH_NEARBY_PCT if nearby else MATCH_SAME_DAY_PCT))
    return max(floor, abs(a) * pct, abs(b) * pct)


def match_trades_to_cashflows(
    trades: List[Dict[str, Any]],
    cashflows: List[Dict[str, Any]],
    *,
    amount_tol: Decimal = AMOUNT_TOLERANCE,
    settlement_days: int = SETTLEMENT_DAYS,
) -> List[Dict[str, Any]]:
    """Return missing-trade rows. Same-day first, then ±settlement_days. Does not touch the DB."""
    cf_by_day: Dict[Tuple[int, date], List[Dict[str, Any]]] = defaultdict(list)
    unused_by_client: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for cf in cashflows:
        d = cf.get("date")
        if d is None:
            continue
        row = dict(cf)
        row["_signed"] = _cf_signed(row)
        cf_by_day[(int(cf["client_id"]), d)].append(row)
        unused_by_client[int(cf["client_id"])].append(row)

    tx_by_day: Dict[Tuple[int, date], List[Dict[str, Any]]] = defaultdict(list)
    for tx in trades:
        d = tx.get("date")
        if d is None:
            continue
        tx_by_day[(int(tx["client_id"]), d)].append(tx)

    def _pop_match(unused: List[Dict[str, Any]], predicate) -> Optional[Dict[str, Any]]:
        for i, cf in enumerate(unused):
            if predicate(cf):
                return unused.pop(i)
        return None

    missing: List[Dict[str, Any]] = []
    leftover_by_key: Dict[Tuple[int, date], List[Dict[str, Any]]] = {}

    for key, day_trades in tx_by_day.items():
        client_id, day = key
        unused = list(cf_by_day.get(key, []))
        day_cf_net = sum((c["_signed"] for c in unused), Decimal("0"))
        day_trade_net = sum((_dec(t["expected_cf_amount"]) for t in day_trades), Decimal("0"))
        same_day_ids = [str(c["id"]) for c in unused]
        leftover: List[Dict[str, Any]] = []
        line_tol = max(amount_tol, LINE_AMOUNT_FLOOR)

        for tx in sorted(day_trades, key=lambda r: int(r["transaction_id"])):
            expected = _dec(tx["expected_cf_amount"])
            descs = set(auto_descriptions(tx["type"], tx["quantity"], tx.get("symbol") or ""))
            match = _pop_match(unused, lambda c: (c.get("description") or "").strip() in descs)
            if match is None:
                tol = max(line_tol, _line_amount_tol(expected))
                match = _pop_match(unused, lambda c, e=expected, t=tol: abs(c["_signed"] - e) <= t)
            if match is not None:
                # Keep client unused list in sync
                client_unused = unused_by_client.get(client_id) or []
                for i, c in enumerate(client_unused):
                    if c.get("id") == match.get("id"):
                        client_unused.pop(i)
                        break
            else:
                leftover.append(tx)

        leftover_trade_net = sum((_dec(t["expected_cf_amount"]) for t in leftover), Decimal("0"))
        leftover_cf_net = sum((c["_signed"] for c in unused), Decimal("0"))
        day_band = _day_net_tol(leftover_trade_net, leftover_cf_net)
        if leftover and abs(leftover_trade_net - leftover_cf_net) <= day_band and leftover_cf_net != 0:
            for c in unused:
                client_unused = unused_by_client.get(client_id) or []
                for i, u in enumerate(list(client_unused)):
                    if u.get("id") == c.get("id"):
                        client_unused.pop(i)
            continue
        leftover_by_key[key] = leftover
        # Stash same-day context for later
        for tx in leftover:
            tx["_same_day_cashflow_count"] = len(cf_by_day.get(key, []))
            tx["_same_day_cashflow_net"] = f"{day_cf_net:.2f}"
            tx["_same_day_trade_net"] = f"{day_trade_net:.2f}"
            tx["_same_day_cashflow_ids"] = ",".join(same_day_ids)
            tx["_day_gap"] = f"{(day_cf_net - day_trade_net):.2f}"
            tx["_had_same_day_cf"] = bool(same_day_ids)

    # Second pass: leftover trades vs unused CFs within ±settlement_days
    for key, leftover in leftover_by_key.items():
        client_id, day = key
        client_unused = unused_by_client.get(client_id) or []
        remaining: List[Dict[str, Any]] = []
        for tx in leftover:
            expected = _dec(tx["expected_cf_amount"])
            tol = max(amount_tol, _line_amount_tol(expected), _day_net_tol(expected, expected, nearby=True))
            candidates = []
            for i, cf in enumerate(client_unused):
                cd = cf.get("date")
                if cd is None:
                    continue
                delta = abs((cd - day).days)
                if delta == 0 or delta > settlement_days:
                    continue
                if abs(cf["_signed"] - expected) <= tol:
                    candidates.append((delta, i, cf))
            if candidates:
                candidates.sort(key=lambda t: (t[0], t[1]))
                _, idx, _hit = candidates[0]
                client_unused.pop(idx)
                continue
            remaining.append(tx)
        leftover_by_key[key] = remaining

    for key, leftover in leftover_by_key.items():
        for tx in leftover:
            row = dict(tx)
            row["same_day_cashflow_count"] = row.pop("_same_day_cashflow_count", 0)
            row["same_day_cashflow_net"] = row.pop("_same_day_cashflow_net", "0.00")
            row["same_day_trade_net"] = row.pop("_same_day_trade_net", "0.00")
            row["same_day_cashflow_ids"] = row.pop("_same_day_cashflow_ids", "")
            row["day_gap"] = row.pop("_day_gap", "0.00")
            had_cf = row.pop("_had_same_day_cf", False)
            if not had_cf:
                row["reason"] = "no_same_day_cashflow"
            else:
                try:
                    gap = abs(Decimal(str(row["same_day_cashflow_net"])) - Decimal(str(row["same_day_trade_net"])))
                except Exception:
                    gap = Decimal("1")
                row["reason"] = (
                    "same_day_amount_mismatch"
                    if gap > amount_tol
                    else "no_matching_cashflow_line"
                )
            missing.append(row)
    return missing


def find_trades_missing_cashflow(
    start: date,
    end: date,
    *,
    active_only: bool = True,
    include_created_in_window: bool = True,
) -> Tuple[List[Dict[str, Any]], int]:
    """Load BUY/SELL trades in the window and return those without a matching cashflow."""
    from sqlalchemy import func, or_

    from models import Cashflow, Client, Security, Transaction

    start_dt = datetime.combine(start, datetime.min.time())
    end_exclusive = datetime.combine(end + timedelta(days=1), datetime.min.time())

    date_in_window = (
        (func.date(Transaction.transaction_date) >= start)
        & (func.date(Transaction.transaction_date) <= end)
    )
    created_in_window = (
        Transaction.created_at.isnot(None)
        & (Transaction.created_at >= start_dt)
        & (Transaction.created_at < end_exclusive)
    )
    window_filter = (
        or_(date_in_window, created_in_window) if include_created_in_window else date_in_window
    )

    q = (
        Transaction.query.join(Client, Client.id == Transaction.client_id)
        .outerjoin(Security, Security.id == Transaction.security_id)
        .filter(func.upper(Transaction.type).in_(["BUY", "SELL"]))
        .filter(window_filter)
    )
    if active_only:
        q = q.filter(Client.is_active.is_(True))

    tx_rows = q.with_entities(
        Transaction.id,
        Transaction.client_id,
        Client.name,
        Client.is_active,
        Transaction.security_id,
        Security.symbol,
        Transaction.type,
        Transaction.quantity,
        Transaction.price,
        Transaction.amount,
        Transaction.transaction_date,
        Transaction.created_at,
        Transaction.notes,
    ).all()

    trades: List[Dict[str, Any]] = []
    client_ids = set()
    trade_dates: List[date] = []
    for r in tx_rows:
        d = _as_date(r.transaction_date)
        if d in DUMMY_DATES:
            continue
        client_ids.add(int(r.client_id))
        if d is not None:
            trade_dates.append(d)
        expected = expected_signed_amount(r.type, r.amount, r.quantity, r.price)
        trades.append(
            {
                "transaction_id": int(r.id),
                "client_id": int(r.client_id),
                "client_name": r.name or "",
                "client_active": bool(r.is_active),
                "security_id": r.security_id,
                "symbol": r.symbol or "",
                "type": (r.type or "").upper(),
                "quantity": r.quantity,
                "price": r.price,
                "trade_amount": r.amount,
                "expected_cf_amount": expected,
                "date": d,
                "transaction_date": d.isoformat() if d else "",
                "created_at": r.created_at.isoformat(sep=" ") if r.created_at else "",
                "notes": (r.notes or "").replace("\n", " ")[:200],
                "trade_edit_url": f"/transaction/{int(r.id)}/edit",
                "cashflows_url": (
                    f"/cashflows?client_id={int(r.client_id)}"
                    f"&start_date={d.isoformat()}&end_date={d.isoformat()}"
                    if d
                    else f"/cashflows?client_id={int(r.client_id)}"
                ),
            }
        )

    cashflows: List[Dict[str, Any]] = []
    if client_ids and trade_dates:
        cf_start = min(min(trade_dates), start) - timedelta(days=SETTLEMENT_DAYS)
        cf_end = max(max(trade_dates), end) + timedelta(days=SETTLEMENT_DAYS)
        cf_q = Cashflow.query.filter(
            Cashflow.client_id.in_(client_ids),
            func.date(Cashflow.date) >= cf_start,
            func.date(Cashflow.date) <= cf_end,
        )
        for cf in cf_q.all():
            d = _as_date(cf.date)
            if d in DUMMY_DATES:
                continue
            cashflows.append(
                {
                    "id": int(cf.id),
                    "client_id": int(cf.client_id),
                    "amount": cf.amount,
                    "description": cf.description or "",
                    "date": d,
                    "type": cf.type,
                }
            )
    return match_trades_to_cashflows(trades, cashflows), len(trades)
