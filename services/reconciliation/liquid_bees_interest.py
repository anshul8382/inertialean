"""
Liquid Bees (LIQUIDBEES) gaps from interest/dividend credited as units.

When statement > system and |Δ| ≈ estimated interest units (~4% p.a. on
avg units held), recommend booking Δ units at ~₹0 (dividend units).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Annualised interest / dividend yield credited as units (user heuristic).
DEFAULT_ANNUAL_RATE = 0.04
# How close Δ must be to the estimate (relative + absolute floor).
REL_TOL = 0.35
ABS_TOL = 2.0

_LIQUID_NAMES = (
    "LIQUIDBEES",
    "LIQUIDBEES.NS",
    "NIPPONINDIAETFLIQUIDBEES",
    "LIQUID BEES",
    "LIQUIDBEE",
)


def is_liquid_bees_symbol(symbol: Optional[str], name: Optional[str] = None) -> bool:
    s = (symbol or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    n = (name or "").upper().replace(" ", "")
    if not s and not n:
        return False
    if "LIQUIDBEES" in s.replace(" ", "") or "LIQUIDBEE" in s.replace(" ", ""):
        return True
    if "LIQUIDBEES" in n or ("LIQUID" in n and "BEE" in n):
        return True
    for tok in _LIQUID_NAMES:
        if tok.replace(" ", "") in s.replace(" ", "") or tok.replace(" ", "") in n:
            return True
    return False


def _as_date(val) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except Exception:
        return None


def time_weighted_avg_qty(
    trades: List[Dict[str, Any]],
    *,
    as_of: date,
    start: Optional[date] = None,
) -> Tuple[float, int, Optional[date]]:
    """
    Time-weighted average units from BUY/SELL history up to as_of.
    Returns (avg_qty, period_days, first_date).
    """
    events: List[Tuple[date, float]] = []
    for t in trades or []:
        d = _as_date(t.get("date") or t.get("trade_date") or t.get("transaction_date"))
        if not d or d > as_of:
            continue
        q = t.get("quantity")
        if q is None:
            continue
        signed = float(q)
        typ = (t.get("type") or t.get("action") or "").upper()
        if typ in ("SELL", "S"):
            signed = -signed
        elif typ not in ("BUY", "B", "PURCHASE", ""):
            # dividend / credit often booked as BUY already
            pass
        events.append((d, signed))
    events.sort(key=lambda x: x[0])
    if not events:
        return 0.0, 0, None

    first = events[0][0]
    window_start = start or first
    if window_start > as_of:
        return 0.0, 0, first

    qty = 0.0
    # apply events before window
    i = 0
    while i < len(events) and events[i][0] < window_start:
        qty += events[i][1]
        i += 1

    cursor = window_start
    weighted = 0.0
    while i < len(events):
        d, dq = events[i]
        if d > as_of:
            break
        days = (d - cursor).days
        if days > 0 and qty > 0:
            weighted += qty * days
        elif days > 0:
            weighted += max(qty, 0.0) * days
        qty += dq
        cursor = d
        i += 1
    days_end = (as_of - cursor).days
    if days_end > 0:
        weighted += max(qty, 0.0) * days_end
    period = max(1, (as_of - window_start).days)
    avg = weighted / period if period else max(qty, 0.0)
    return float(avg), int(period), first


def estimate_interest_units(
    avg_qty: float,
    period_days: int,
    *,
    annual_rate: float = DEFAULT_ANNUAL_RATE,
) -> float:
    """Units credited ≈ avg_qty × rate × (days/365)."""
    if avg_qty <= 0 or period_days <= 0:
        return 0.0
    return float(avg_qty) * float(annual_rate) * (float(period_days) / 365.0)


def delta_matches_interest(
    delta: float,
    estimated_units: float,
    *,
    rel_tol: float = REL_TOL,
    abs_tol: float = ABS_TOL,
) -> bool:
    """Statement ahead of system (Δ>0) and Δ ≈ interest units."""
    if delta <= 0.001 or estimated_units <= 0:
        return False
    gap = abs(float(delta))
    est = float(estimated_units)
    tol = max(abs_tol, rel_tol * est, rel_tol * gap)
    return abs(gap - est) <= tol


def assess_liquid_bees_gap(
    *,
    symbol: str,
    statement_qty: float,
    system_qty: float,
    delta: float,
    as_of: date,
    trades: Optional[List[Dict[str, Any]]] = None,
    security_name: Optional[str] = None,
    annual_rate: float = DEFAULT_ANNUAL_RATE,
) -> Optional[Dict[str, Any]]:
    """
    If LIQUIDBEES and system lag looks like interest units, return advice pack.
    """
    if not is_liquid_bees_symbol(symbol, security_name):
        return None
    dlt = float(delta if delta is not None else (statement_qty - system_qty))
    # Only when holdings ahead of books (credit not booked)
    if dlt <= 0.001:
        return {
            "applicable": True,
            "matches": False,
            "symbol": (symbol or "").upper(),
            "reason": "system_not_behind",
            "message": (
                "Liquid Bees: system is not behind statement — "
                "interest-unit credit explanation does not apply."
            ),
        }

    trades = list(trades or [])
    # Prefer last 365d window ending as_of; else full history
    start_365 = as_of - timedelta(days=365)
    avg, period_days, first = time_weighted_avg_qty(
        trades, as_of=as_of, start=start_365
    )
    if avg <= 0.01:
        # Fallback: midpoint of statement/system as stand-in avg holding
        avg = max(0.0, (float(statement_qty) + float(system_qty)) / 2.0)
        period_days = 365
        first = first
    est = estimate_interest_units(avg, period_days, annual_rate=annual_rate)
    # Also try full history if 365d estimate is far from delta
    if trades and not delta_matches_interest(dlt, est):
        avg2, days2, first2 = time_weighted_avg_qty(trades, as_of=as_of, start=None)
        est2 = estimate_interest_units(avg2, days2, annual_rate=annual_rate)
        if delta_matches_interest(dlt, est2):
            avg, period_days, first, est = avg2, days2, first2, est2

    matches = delta_matches_interest(dlt, est)
    gap = abs(dlt)
    advice = (
        f"Book BUY {gap:g} units of {(symbol or 'LIQUIDBEES').upper()} at ~₹0 "
        f"(dividend / interest units). System is behind holdings by {gap:g}; "
        f"~{annual_rate*100:.0f}% p.a. on avg ~{avg:.1f} units over {period_days}d "
        f"≈ {est:.1f} units."
    )
    return {
        "applicable": True,
        "matches": matches,
        "symbol": (symbol or "").upper(),
        "delta": dlt,
        "gap": gap,
        "avg_qty": round(avg, 4),
        "period_days": period_days,
        "first_trade_date": first.isoformat() if first else None,
        "annual_rate": annual_rate,
        "estimated_interest_units": round(est, 4),
        "hypothesis": "LIQUIDBEES_INTEREST_UNITS" if matches else None,
        "recommendation": advice if matches else None,
        "message": (
            advice
            if matches
            else (
                f"Liquid Bees: system behind by {gap:g}; estimated interest units "
                f"≈ {est:.1f} (avg {avg:.1f} × {annual_rate*100:.0f}% × {period_days}/365). "
                f"Δ is not close enough to auto-recommend a ₹0 entry."
            )
        ),
    }


def fetch_liquid_bees_trades(
    client_id: int, security_id: int, as_of: date
) -> List[Dict[str, Any]]:
    """All trades for security up to as_of (for avg holding)."""
    from models import Transaction

    end = datetime.combine(as_of, datetime.max.time())
    rows = (
        Transaction.query.filter(
            Transaction.client_id == int(client_id),
            Transaction.security_id == int(security_id),
            Transaction.transaction_date <= end,
        )
        .order_by(Transaction.transaction_date.asc())
        .limit(500)
        .all()
    )
    out = []
    for t in rows:
        td = t.transaction_date.date() if t.transaction_date else None
        out.append(
            {
                "id": t.id,
                "date": td.isoformat() if td else None,
                "type": t.type,
                "quantity": float(t.quantity) if t.quantity is not None else None,
                "price": float(t.price) if t.price is not None else None,
            }
        )
    return out


def apply_liquid_bees_to_breaks(
    client_id: int,
    breaks: List[Dict[str, Any]],
    *,
    as_of: date,
) -> Dict[str, Any]:
    """Annotate Liquid Bees breaks; override hypothesis when Δ ≈ interest."""
    hits = []
    for b in breaks or []:
        sym = b.get("symbol")
        if not is_liquid_bees_symbol(sym):
            continue
        sid = b.get("security_id")
        trades = list(
            ((b.get("diagnosis") or {}).get("trade_story") or {}).get("recent_trades")
            or []
        )
        if sid:
            try:
                full = fetch_liquid_bees_trades(int(client_id), int(sid), as_of)
                if full:
                    trades = full
            except Exception:
                pass
        pack = assess_liquid_bees_gap(
            symbol=sym or "",
            statement_qty=float(b.get("statement_qty") or 0),
            system_qty=float(b.get("system_qty") or 0),
            delta=float(b.get("delta") or 0),
            as_of=as_of,
            trades=trades,
        )
        if not pack:
            continue
        d = dict(b.get("diagnosis") or {})
        d["liquid_bees"] = pack
        notes = list(d.get("investigation_notes") or [])
        if pack.get("message"):
            notes.append(pack["message"])
        if pack.get("matches"):
            if d.get("top_hypothesis") and not d.get("prior_hypothesis"):
                d["prior_hypothesis"] = d.get("top_hypothesis")
            d["top_hypothesis"] = "LIQUIDBEES_INTEREST_UNITS"
            d["advisor_action"] = pack.get("recommendation")
            b["status_display"] = "LIQUIDBEES_INTEREST_UNITS"
            # Keep match status; display uses status_display in UI where set
            hits.append(
                {
                    "symbol": sym,
                    "gap": pack.get("gap"),
                    "estimated_interest_units": pack.get("estimated_interest_units"),
                    "recommendation": pack.get("recommendation"),
                }
            )
        d["investigation_notes"] = notes
        b["diagnosis"] = d

    return {
        "checked": True,
        "hits": hits,
        "summary": (
            f"{len(hits)} Liquid Bees gap(s) look like interest/dividend units "
            f"— book Δ @ ~₹0."
            if hits
            else "No Liquid Bees interest-unit matches on open gaps."
        ),
    }
