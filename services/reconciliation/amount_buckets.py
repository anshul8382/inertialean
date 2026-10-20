"""
Tentative ₹ amounts in dispute for holdings recon.

Three buckets as % of statement portfolio value (qty × price):
  1. Matched — names that already agree
  2. Found sources — high-conf / FOUND_MISSING_RECOMMENDATION (|Δ|×price)
  3. Needs check — remaining mismatches (|Δ|×price)
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Set


def _norm_sym(sym: Optional[str]) -> str:
    s = (sym or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def _f(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except Exception:
        return None


def lookup_price(
    *,
    symbol: Optional[str],
    security_id: Optional[int],
    prices_by_symbol: Optional[Dict[str, float]] = None,
    row: Optional[Dict[str, Any]] = None,
    as_of: Optional[date] = None,
    fetch_missing: bool = True,
) -> Optional[float]:
    """Prefer map / row fields; optionally PriceService by security_id."""
    prices_by_symbol = prices_by_symbol or {}
    sym = _norm_sym(symbol)
    if sym and sym in prices_by_symbol:
        return float(prices_by_symbol[sym])
    row = row or {}
    for key in ("price", "current_price", "market_price", "last_price"):
        px = _f(row.get(key))
        if px and px > 0:
            return px
    if not fetch_missing or not security_id:
        return None
    try:
        from services.price_service import PriceService

        data = PriceService.get_price(int(security_id), target_date=as_of)
        if data is None:
            return None
        px = _f(getattr(data, "price", None) or data)
        if px and px > 0:
            return px
    except Exception:
        return None
    return None


def build_amount_dispute_summary(
    *,
    matched_rows: List[Dict[str, Any]],
    breaks: List[Dict[str, Any]],
    advisor_story: Optional[Dict[str, Any]] = None,
    prices_by_symbol: Optional[Dict[str, float]] = None,
    as_of: Optional[date] = None,
    fetch_missing_prices: bool = True,
) -> Dict[str, Any]:
    """
    Compute matched / found-sources / needs-check ₹ buckets.

    Portfolio value = Σ statement_qty × price over matched + breaks.
    Found-sources = symbols in high_confidence_symbols / fixes, or status
    FOUND_MISSING_RECOMMENDATION.
    """
    prices = dict(prices_by_symbol or {})
    story = advisor_story or {}
    found_syms: Set[str] = {
        _norm_sym(s)
        for s in (
            story.get("high_confidence_symbols") or story.get("fixes") or []
        )
        if s
    }

    def ensure_price(row: Dict[str, Any]) -> Optional[float]:
        sym = _norm_sym(row.get("symbol"))
        sid = row.get("security_id")
        try:
            sid_i = int(sid) if sid is not None else None
        except Exception:
            sid_i = None
        px = lookup_price(
            symbol=sym,
            security_id=sid_i,
            prices_by_symbol=prices,
            row=row,
            as_of=as_of,
            fetch_missing=fetch_missing_prices,
        )
        if px and sym:
            prices[sym] = px
        return px

    matched_amt = 0.0
    matched_with_price = 0
    matched_missing_price = 0
    for m in matched_rows or []:
        qty = abs(_f(m.get("statement_qty")) or _f(m.get("system_qty")) or 0.0)
        px = ensure_price(m)
        if px and qty:
            amt = qty * px
            matched_amt += amt
            matched_with_price += 1
            m["price"] = round(px, 4)
            m["matched_amount"] = round(amt, 2)
        else:
            matched_missing_price += 1

    found_amt = 0.0
    needs_amt = 0.0
    # Statement portfolio also includes break lines at statement qty
    statement_portfolio = matched_amt
    found_names: List[Dict[str, Any]] = []
    needs_names: List[Dict[str, Any]] = []
    price_gaps = 0

    for b in breaks or []:
        sym = _norm_sym(b.get("symbol"))
        stmt = abs(_f(b.get("statement_qty")) or 0.0)
        delta = abs(_f(b.get("delta")) or 0.0)
        px = ensure_price(b)
        if px and stmt:
            statement_portfolio += stmt * px
        if not px:
            price_gaps += 1
            b["tentative_amount"] = None
            b["price"] = None
            b["amount_bucket"] = "needs_check"
            needs_names.append(
                {
                    "symbol": sym or b.get("security_id"),
                    "delta": _f(b.get("delta")),
                    "amount": None,
                    "price": None,
                }
            )
            continue
        b["price"] = round(px, 4)
        tent = round(delta * px, 2)
        b["tentative_amount"] = tent
        is_found = (
            sym in found_syms
            or (b.get("status") or "") == "FOUND_MISSING_RECOMMENDATION"
            or bool((b.get("diagnosis") or {}).get("high_confidence"))
            or str((b.get("diagnosis") or {}).get("top_hypothesis") or "").startswith(
                "FOUND_MISSING_RECOMMENDATION"
            )
        )
        if is_found:
            found_amt += tent
            b["amount_bucket"] = "found_sources"
            found_names.append(
                {
                    "symbol": sym,
                    "delta": _f(b.get("delta")),
                    "amount": tent,
                    "price": round(px, 4),
                }
            )
        else:
            needs_amt += tent
            b["amount_bucket"] = "needs_check"
            needs_names.append(
                {
                    "symbol": sym,
                    "delta": _f(b.get("delta")),
                    "amount": tent,
                    "price": round(px, 4),
                }
            )

    total_pv = statement_portfolio
    dispute_total = found_amt + needs_amt

    def pct(part: float) -> Optional[float]:
        if not total_pv or total_pv <= 0:
            return None
        return round(100.0 * part / total_pv, 2)

    return {
        "portfolio_value": round(total_pv, 2),
        "portfolio_basis": "statement_qty × price (matched + mismatches)",
        "dispute_total": round(dispute_total, 2),
        "matched": {
            "amount": round(matched_amt, 2),
            "pct_of_portfolio": pct(matched_amt),
            "count": len(matched_rows or []),
            "priced_count": matched_with_price,
            "missing_price_count": matched_missing_price,
            "label": "Matched",
        },
        "found_sources": {
            "amount": round(found_amt, 2),
            "pct_of_portfolio": pct(found_amt),
            "count": len(found_names),
            "symbols": found_names[:40],
            "label": "Found sources",
            "note": "High-confidence gaps explained by reco/source (|Δ|×price)",
        },
        "needs_check": {
            "amount": round(needs_amt, 2),
            "pct_of_portfolio": pct(needs_amt),
            "count": len(needs_names),
            "symbols": needs_names[:40],
            "label": "Still need to check",
            "note": "Needs further analysis (|Δ|×price)",
        },
        "prices_used": prices,
        "missing_price_on_breaks": price_gaps,
        "summary": (
            f"Portfolio ≈ ₹{total_pv:,.0f}: "
            f"matched ₹{matched_amt:,.0f} ({pct(matched_amt) or 0}%), "
            f"found sources ₹{found_amt:,.0f} ({pct(found_amt) or 0}%), "
            f"still need check ₹{needs_amt:,.0f} ({pct(needs_amt) or 0}%)."
            if total_pv
            else "Portfolio value unavailable (missing prices)."
        ),
    }
