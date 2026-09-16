# Inertia Portfolio Review Audit Engine (integrated with Flask app)
# Ported from standalone FastAPI service: price + calculation audits, report gate.
from __future__ import annotations

import logging
import math
import os
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from dateutil import parser as dateparser
from dateutil.relativedelta import relativedelta

logger = logging.getLogger("portfolio_review_audit")

PRICE_TOL_PCT = 1.0
PRICE_ERR_PCT = 3.0
CALC_TOL_PCT = 0.5
CALC_ERR_PCT = 2.0
STALE_DAYS = 5
VERDICT_ORDER = {"red": 0, "amber": 1, "info": 2, "green": 3, "skipped": 4}


@dataclass
class PriceAuditRequest:
    raw_data: str
    client: str = ""
    period_start: str = ""
    period_end: str = ""
    adviser_context: str = ""
    checks: Dict[str, bool] = field(default_factory=dict)
    # When set (full-from-period), PC2 uses exit_date for sold holdings instead of period_end.
    period_analysis: Optional[Dict[str, Any]] = None


@dataclass
class CalcAuditRequest:
    raw_data: str
    client: str = ""
    period_start: str = ""
    period_end: str = ""
    checks: Dict[str, bool] = field(default_factory=dict)
    # When set (e.g. full-from-period), cc5 can use structured pathway + MTM instead of regex-only text.
    period_analysis: Optional[Dict[str, Any]] = None


@dataclass
class FullAuditRequest:
    raw_data: str
    client: str = ""
    period_start: str = ""
    period_end: str = ""
    adviser_context: str = ""
    price_checks: Dict[str, bool] = field(default_factory=dict)
    calc_checks: Dict[str, bool] = field(default_factory=dict)
    overrides: Dict[str, str] = field(default_factory=dict)
    period_analysis: Optional[Dict[str, Any]] = None


@dataclass
class CheckResult:
    id: str
    label: str
    verdict: str
    emoji: str
    reported: Optional[str] = None
    verified: Optional[str] = None
    computed: Optional[str] = None
    diff_pct: Optional[float] = None
    detail: str = ""
    arithmetic: Optional[str] = None
    overrideable: bool = True


@dataclass
class AuditResponse:
    phase: str
    client: str
    period: str
    run_at: str
    summary: Dict[str, int]
    checks: List[CheckResult]
    overall_verdict: str
    blocking_ids: List[str]
    notes: List[str]


@dataclass
class GateCheckResponse:
    can_proceed: bool
    blocking_findings: List[Dict[str, str]]
    override_prompt: str


def check_result_to_dict(c: CheckResult) -> Dict[str, Any]:
    return asdict(c)


def audit_response_to_dict(a: AuditResponse) -> Dict[str, Any]:
    return {
        "phase": a.phase,
        "client": a.client,
        "period": a.period,
        "run_at": a.run_at,
        "summary": a.summary,
        "checks": [check_result_to_dict(x) for x in a.checks],
        "overall_verdict": a.overall_verdict,
        "blocking_ids": a.blocking_ids,
        "notes": a.notes,
    }


def gate_response_to_dict(g: GateCheckResponse) -> Dict[str, Any]:
    return {
        "can_proceed": g.can_proceed,
        "blocking_findings": g.blocking_findings,
        "override_prompt": g.override_prompt,
    }


def xirr(cashflows: List[Tuple[date, float]], guess: float = 0.10) -> Optional[float]:
    if len(cashflows) < 2:
        return None
    dates = [cf[0] for cf in cashflows]
    amounts = [cf[1] for cf in cashflows]
    base = dates[0]
    years = [(d - base).days / 365.0 for d in dates]

    def npv(r):
        return sum(a / (1 + r) ** t for a, t in zip(amounts, years))

    def dnpv(r):
        return sum(-t * a / (1 + r) ** (t + 1) for a, t in zip(amounts, years))

    for start_guess in [guess, 0.0, -0.1, 0.5, -0.5]:
        r = start_guess
        try:
            for _ in range(300):
                f = npv(r)
                df = dnpv(r)
                if df == 0:
                    break
                r2 = r - f / df
                if abs(r2 - r) < 1e-8:
                    return r2
                r = r2
        except (ZeroDivisionError, OverflowError):
            continue
    return None


def twr_chain(sub_returns: List[float]) -> float:
    result = 1.0
    for r in sub_returns:
        result *= (1.0 + r)
    return result - 1.0


def business_days_between(d1: date, d2: date) -> int:
    if d1 > d2:
        d1, d2 = d2, d1
    count, cur = 0, d1
    while cur < d2:
        if cur.weekday() < 5:
            count += 1
        cur += timedelta(days=1)
    return count


def parse_inr(text: str) -> Optional[float]:
    if not text:
        return None
    t = str(text).replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    for pat, mult in [(r"([\d.]+)\s*[Cc][Rr]", 1e7), (r"([\d.]+)\s*[Ll]", 1e5), (r"([\d.]+)", 1)]:
        m = re.search(pat, t)
        if m:
            return float(m.group(1)) * mult
    return None


def parse_date_safe(s: str) -> Optional[date]:
    try:
        return dateparser.parse(str(s), dayfirst=False).date()
    except Exception:
        return None


def find_first(pattern: str, text: str, flags=re.IGNORECASE) -> Optional[str]:
    m = re.search(pattern, text, flags)
    return m.group(1) if m else None


def extract_holdings(raw: str) -> List[Dict]:
    holdings = []
    for line in raw.split("\n"):
        m = re.search(
            r"\b([A-Z&]{2,15})\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)\s+([-+]?[\d.]+)%",
            line.strip(),
        )
        if m:
            exit_dt = None
            exit_m = re.search(r"exit=(\d{4}-\d{2}-\d{2})", line, re.IGNORECASE)
            if exit_m:
                exit_dt = parse_date_safe(exit_m.group(1))
            basis_m = re.search(
                r"\bbasis=(period_start_market|period_purchase_vwap|blended|holding_average_cost)\b",
                line,
                re.IGNORECASE,
            )
            holdings.append(
                {
                    "name": m.group(1),
                    "start_price": float(m.group(2).replace(",", "")),
                    "end_price": float(m.group(3).replace(",", "")),
                    "reported_return": float(m.group(4)) / 100.0,
                    "exit_date": exit_dt,
                    "return_basis": basis_m.group(1).lower() if basis_m else None,
                }
            )
    return holdings


def _pc2_holder_meta_by_symbol(
    period_analysis: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Structured holder-return fields from portfolio_holdings_comparison (Period Analysis v2)."""
    out: Dict[str, Dict[str, Any]] = {}
    if not period_analysis:
        return out
    for h in period_analysis.get("portfolio_holdings_comparison") or []:
        sym = (h.get("symbol") or "").upper().strip()
        if not sym:
            continue
        st = h.get("start") or {}
        out[sym] = {
            "return_basis": (h.get("return_basis") or "period_start_market"),
            "period_start_market_price": h.get("period_start_market_price"),
            "period_buy_vwap": h.get("period_buy_vwap"),
            "start_qty": float(st.get("quantity") or 0),
        }
    return out


def _pc2_uses_entry_start_price(return_basis: Optional[str]) -> bool:
    basis = (return_basis or "period_start_market").lower()
    return basis != "period_start_market"


def _pc2_verify_entry_start_price(
    name: str,
    reported_p: float,
    meta: Dict[str, Any],
    arith_lines: List[str],
    red_items: List[str],
    amber_items: List[str],
) -> None:
    """
    Start price is effective entry (VWAP / blended), not period-start market close.
    Cross-check against period BUY VWAP when available; never compare to period-start close.
    """
    basis = (meta.get("return_basis") or "period_purchase_vwap").lower()
    psm = meta.get("period_start_market_price")
    vwap = meta.get("period_buy_vwap")
    try:
        vwap_f = float(vwap) if vwap is not None else 0.0
    except (TypeError, ValueError):
        vwap_f = 0.0

    if basis == "blended":
        psm_note = f" (period-start market ₹{float(psm):,.2f})" if psm else ""
        arith_lines.append(
            f"{name} start: blended effective entry ₹{reported_p:,.2f}{psm_note} "
            f"— not verified vs period-start market close"
        )
        return

    if basis == "holding_average_cost":
        arith_lines.append(
            f"{name} start: average-cost entry ₹{reported_p:,.2f} (basis={basis}) "
            f"— not verified vs period-start market close"
        )
        return

    if vwap_f > 0:
        diff = abs(reported_p - vwap_f) / vwap_f * 100
        v = verdict_from_diff(diff)
        arith_lines.append(
            f"{name} start (entry): reported={reported_p:,.2f}  period BUY VWAP={vwap_f:,.2f}  "
            f"diff={diff:.2f}%  → {v.upper()}"
        )
        if v == "red":
            red_items.append(f"{name} entry start ({diff:.1f}% off VWAP)")
        elif v == "amber":
            amber_items.append(f"{name} entry start ({diff:.1f}% off VWAP)")
    else:
        arith_lines.append(
            f"{name} start: entry price ₹{reported_p:,.2f} (basis={basis}) "
            f"— no period BUY VWAP in payload to cross-check"
        )


def _pc2_exit_dates_by_symbol(period_analysis: Optional[Dict[str, Any]]) -> Dict[str, date]:
    """
    Map symbol -> date to verify end price for holdings sold/exited during the period.
    """
    out: Dict[str, date] = {}
    if not period_analysis:
        return out
    for h in period_analysis.get("portfolio_holdings_comparison") or []:
        sym = (h.get("symbol") or "").upper().strip()
        if not sym:
            continue
        st = h.get("start") or {}
        en = h.get("end") or {}
        start_qty = float(st.get("quantity") or 0)
        end_qty = float(en.get("quantity") or 0)
        if start_qty <= 0 or end_qty > 0:
            continue
        exit_raw = (
            h.get("exit_date")
            or en.get("effective_end_date")
            or h.get("effective_end_date")
        )
        if not exit_raw:
            continue
        exit_dt = parse_date_safe(str(exit_raw))
        if exit_dt:
            out[sym] = exit_dt
    return out


def extract_cashflows(raw: str) -> List[Tuple[date, float]]:
    date_pat = re.compile(
        r"(\d{1,2}[-/\s][A-Za-z]{3}[-/\s]\d{4}|\d{4}[-/]\d{2}[-/]\d{2}|[A-Za-z]{3}\s+\d{1,2},?\s+\d{4})"
    )
    amount_pat = re.compile(r"([-+]?[\d,]+\.?\d*)")
    results = []
    for line in raw.split("\n"):
        dm = date_pat.search(line)
        if not dm:
            continue
        d = parse_date_safe(dm.group(1))
        if not d:
            continue
        rest = line[dm.end() :]
        amounts = amount_pat.findall(rest)
        if amounts:
            try:
                amt = float(amounts[0].replace(",", ""))
                if abs(amt) > 1000:
                    results.append((d, amt))
            except ValueError:
                pass
    return results


def _normalize_sheets_symbol(symbol: str) -> str:
    u = symbol.upper().strip().replace(".NS", "").replace(".BO", "")
    if u in ("^NSEI", "NSEI", "NIFTY", "NIFTY50", "NIFTY 50"):
        return "NIFTY"
    return u


def _audit_price_backends() -> Tuple[str, ...]:
    """
    Order of backends for audit prices (no Yahoo / yfinance).

    PORTFOLIO_AUDIT_PRICE_SOURCE:
      - db_sheets (default) — database then Google Sheets
      - sheets_db — Google Sheets then database
    """
    raw = (os.environ.get("PORTFOLIO_AUDIT_PRICE_SOURCE") or "db_sheets").strip().lower()
    if raw == "sheets_db":
        return ("google_sheets", "db")
    return ("db", "google_sheets")


def _lookup_security_id(symbol_plain: str) -> Optional[int]:
    try:
        from sqlalchemy import func

        from models import Security

        s = symbol_plain.upper().strip().replace(".NS", "").replace(".BO", "")
        for candidate in (s, f"{s}.NS", f"{s}.BO"):
            sec = Security.query.filter(Security.symbol == candidate).first()
            if sec:
                return int(sec.id)
        sec = Security.query.filter(func.upper(Security.symbol) == s).first()
        return int(sec.id) if sec else None
    except Exception as ex:
        logger.debug("audit security lookup skipped: %s", ex)
        return None


def _fetch_close_db(symbol: str, target_date: date) -> Tuple[Optional[float], str]:
    try:
        from sqlalchemy import desc

        from models import BenchmarkData

        u = symbol.upper().strip()
        if u in ("^NSEI", "NSEI", "NIFTY", "NIFTY50", "NIFTY 50"):
            row = (
                BenchmarkData.query.filter(
                    BenchmarkData.benchmark_id == 1,
                    BenchmarkData.date <= target_date,
                )
                .order_by(desc(BenchmarkData.date))
                .first()
            )
            if row and row.price is not None:
                return float(row.price), str(row.date)
            return None, "no_data"

        sid = _lookup_security_id(u)
        if not sid:
            return None, "no_data"
        from services.price_service import PriceService

        # Match adviser reports / holdings tables: prices on a current share basis after splits & bonuses.
        pd = PriceService.get_price(sid, target_date, use_adjusted=True, allow_fallback=True)
        if pd and pd.price is not None and pd.is_valid:
            used = pd.last_updated if isinstance(pd.last_updated, date) else target_date
            return float(pd.price), str(used)
    except Exception as ex:
        logger.debug("audit DB close failed %s %s: %s", symbol, target_date, ex)
    return None, "no_data"


def _fetch_close_sheets(symbol: str, target_date: date) -> Tuple[Optional[float], str]:
    try:
        from services.google_sheets_historical_price import GoogleSheetsHistoricalPrice
        from services.price_service import PriceService

        sym = _normalize_sheets_symbol(symbol)
        if not sym or sym == "INVALID":
            return None, "no_data"
        r = GoogleSheetsHistoricalPrice.get_historical_price(sym, target_date)
        if r and r.get("price") is not None:
            d_raw = r.get("date") or target_date
            if isinstance(d_raw, datetime):
                d = d_raw.date()
            elif isinstance(d_raw, date):
                d = d_raw
            else:
                d = parse_date_safe(str(d_raw)[:10]) or target_date
            price = float(r["price"])
            sid = _lookup_security_id(symbol)
            if sid:
                price = PriceService.restate_historical_price_to_current_basis(sid, price, d)
            ds = d.isoformat() if isinstance(d, date) else str(d)[:10]
            return price, ds
    except FileNotFoundError:
        logger.info("audit Google Sheets: service_account.json not found; skip sheets")
    except Exception as ex:
        logger.warning("audit Google Sheets close failed %s %s: %s", symbol, target_date, ex)
    return None, "no_data"


def fetch_closing_price(symbol: str, target_date: date) -> Tuple[Optional[float], str]:
    tried: List[str] = []
    for backend in _audit_price_backends():
        tried.append(backend)
        if backend == "db":
            p, src = _fetch_close_db(symbol, target_date)
        else:
            p, src = _fetch_close_sheets(symbol, target_date)
        if p is not None:
            return p, src
    return None, "no_data:" + ",".join(tried)


def _fetch_range_db(symbol: str, start: date, end: date) -> Tuple[Optional[float], Optional[float]]:
    """
    Period low/high on current share basis. Raw SQL min/max across dates is wrong when a split
    lands inside the window; we restate each bar then take min(low) / max(high).
    """
    try:
        from models import HistoricalPrice
        from services.price_service import PriceService

        sid = _lookup_security_id(symbol)
        if not sid:
            return None, None
        rows = (
            HistoricalPrice.query.filter(
                HistoricalPrice.security_id == sid,
                HistoricalPrice.date >= start,
                HistoricalPrice.date <= end,
            )
            .order_by(HistoricalPrice.date)
            .all()
        )
        if not rows:
            return None, None
        adj_lows: List[float] = []
        adj_highs: List[float] = []
        for row in rows:
            d = row.date
            if row.low_price is not None and row.high_price is not None:
                adj_lows.append(
                    PriceService.restate_historical_price_to_current_basis(
                        sid, float(row.low_price), d
                    )
                )
                adj_highs.append(
                    PriceService.restate_historical_price_to_current_basis(
                        sid, float(row.high_price), d
                    )
                )
            elif row.close_price is not None:
                c = PriceService.restate_historical_price_to_current_basis(
                    sid, float(row.close_price), d
                )
                adj_lows.append(c)
                adj_highs.append(c)
        if not adj_lows:
            return None, None
        return min(adj_lows), max(adj_highs)
    except Exception as ex:
        logger.debug("audit DB range failed %s: %s", symbol, ex)
    return None, None


def fetch_price_range(symbol: str, start: date, end: date) -> Tuple[Optional[float], Optional[float]]:
    """Period low/high from historical_price, restated to current share basis (splits/bonuses)."""
    return _fetch_range_db(symbol, start, end)


def _record_price_miss(
    missing: Dict[Tuple[str, str], Set[str]],
    symbol: str,
    dt: Optional[date],
    context: str,
) -> None:
    """Track (SYMBOL, YYYY-MM-DD) pairs where no closing / range price was available."""
    if not dt or not symbol:
        return
    sym = str(symbol).strip().upper().replace("^NSEI", "NIFTY")
    missing[(sym, dt.isoformat())].add(context)


def _record_price_range_gap(
    missing_ranges: Dict[Tuple[str, date, date], Set[str]],
    symbol: str,
    start: date,
    end: date,
    context: str,
) -> None:
    """Track an inclusive calendar date range with no usable prices (e.g. PC3 whole-period OHLC gap)."""
    if not symbol or not start or not end:
        return
    sym = str(symbol).strip().upper().replace("^NSEI", "NIFTY")
    if end < start:
        start, end = end, start
    missing_ranges[(sym, start, end)].add(context)


def _merge_inclusive_date_intervals(intervals: List[Tuple[date, date]]) -> List[Tuple[date, date]]:
    """Merge overlapping or calendar-adjacent inclusive [start, end] intervals."""
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: (x[0], x[1]))
    out: List[Tuple[date, date]] = [intervals[0]]
    for s, e in intervals[1:]:
        ps, pe = out[-1]
        if s <= pe + timedelta(days=1):
            out[-1] = (ps, max(pe, e))
        else:
            out.append((s, e))
    return out


def _build_calc_impacting_price_gap_lines(
    missing_prices: Dict[Tuple[str, str], Set[str]],
    missing_ranges: Dict[Tuple[str, date, date], Set[str]],
) -> Tuple[List[str], int]:
    """
    One line per symbol per merged gap. Consecutive missing calendar dates collapse to a single range.
    Only includes what was recorded for PC1/PC2/PC3 price verification.
    """
    by_sym_dates: Dict[str, List[date]] = defaultdict(list)
    for (sym, dts), _ctx in missing_prices.items():
        try:
            by_sym_dates[sym].append(date.fromisoformat(dts))
        except ValueError:
            continue
    by_sym_ranges: Dict[str, List[Tuple[date, date]]] = defaultdict(list)
    for (sym, d0, d1), _ctx in missing_ranges.items():
        by_sym_ranges[sym].append((d0, d1))

    rows: List[str] = []
    for sym in sorted(set(by_sym_dates) | set(by_sym_ranges)):
        intervals = [(d, d) for d in by_sym_dates.get(sym, [])] + list(by_sym_ranges.get(sym, []))
        merged = _merge_inclusive_date_intervals(intervals)
        for a, b in merged:
            ctx_union: Set[str] = set()
            for (s2, dts), ctxs in missing_prices.items():
                if s2 != sym:
                    continue
                try:
                    dd = date.fromisoformat(dts)
                except ValueError:
                    continue
                if a <= dd <= b:
                    ctx_union |= ctxs
            for (s2, d0, d1), ctxs in missing_ranges.items():
                if s2 != sym:
                    continue
                if d1 < a or d0 > b:
                    continue
                ctx_union |= ctxs
            ctx_line = ", ".join(sorted(ctx_union)) if ctx_union else "price verification"
            if a == b:
                rows.append(f"{sym:16} {a.isoformat()}  — {ctx_line}")
            else:
                rows.append(f"{sym:16} {a.isoformat()} to {b.isoformat()}  — {ctx_line}")
    return rows, len(rows)


# Corporate-action audit helpers (pc4 / pc5) — re-export shared CA restatement helpers
from services.corporate_action_price_restatement import (  # noqa: E402
    CA_PRICE_ACTION_TYPES as CA_AUDIT_ACTION_TYPES,
    compute_adjusted_period_return,
    format_ca_action_summary as _format_ca_action_summary,
    lookup_corporate_actions_for_security,
    lookup_corporate_actions_for_symbol,
    restate_reported_prices_with_actions,
    restatement_factor_from_actions,
)

LARGE_LOSS_PCT = -40.0


def _ca_action_date(action_date: Any) -> Optional[date]:
    from services.corporate_action_price_restatement import ca_action_date

    return ca_action_date(action_date)


def _pc5_adjusted_from_period_analysis(
    period_analysis: Optional[Dict[str, Any]], symbol: str
) -> Optional[Dict[str, float]]:
    """Prefer structured adjusted fields from Period Analysis when present."""
    if not period_analysis:
        return None
    sym = (symbol or "").upper().strip()
    for h in period_analysis.get("portfolio_holdings_comparison") or []:
        if (h.get("symbol") or "").upper().strip() != sym:
            continue
        st = h.get("start") or {}
        en = h.get("end") or {}
        start_px = float(st.get("price_adjusted") or 0)
        end_px = float(en.get("price_adjusted") or 0)
        ret = h.get("price_return_percent_adjusted")
        if ret is None:
            ch = h.get("change") or {}
            ret = ch.get("price_return_percent_adjusted")
        if start_px > 0 and end_px > 0:
            if ret is None:
                ret = (end_px - start_px) / start_px * 100.0
            return {
                "start_price": start_px,
                "end_price": end_px,
                "price_change": end_px - start_px,
                "price_percent_change": float(ret),
            }
    for wp in (period_analysis.get("worst_performers") or {}).get("worst_performers") or []:
        if (wp.get("symbol") or "").upper().strip() != sym:
            continue
        start_px = float(wp.get("start_price") or 0)
        end_px = float(wp.get("end_price") or wp.get("current_price") or 0)
        ret = wp.get("price_percent_change")
        if start_px > 0 and end_px > 0 and ret is not None:
            return {
                "start_price": start_px,
                "end_price": end_px,
                "price_change": end_px - start_px,
                "price_percent_change": float(ret),
            }
    return None


def collect_large_loss_symbols(
    raw: str,
    period_analysis: Optional[Dict[str, Any]] = None,
    threshold_pct: float = LARGE_LOSS_PCT,
) -> List[Dict[str, Any]]:
    """
    Symbols with reported return ≤ threshold (default −40%).
    Sources: audit raw holdings lines, period_analysis holdings, worst_performers.
    """
    by_sym: Dict[str, Dict[str, Any]] = {}

    def _add(
        sym: str,
        ret: Optional[float],
        exit_date: Optional[date] = None,
        start_price: Optional[float] = None,
        end_price: Optional[float] = None,
    ) -> None:
        key = (sym or "").upper().strip()
        if not key:
            return
        prev = by_sym.get(key)
        if prev is None:
            by_sym[key] = {
                "name": key,
                "ret": ret,
                "exit_date": exit_date,
                "start_price": start_price,
                "end_price": end_price,
            }
            return
        if ret is not None and (prev.get("ret") is None or ret < prev["ret"]):
            prev["ret"] = ret
        if exit_date and not prev.get("exit_date"):
            prev["exit_date"] = exit_date
        if start_price and not prev.get("start_price"):
            prev["start_price"] = start_price
        if end_price and not prev.get("end_price"):
            prev["end_price"] = end_price

    for h in extract_holdings(raw):
        ret_pct = float(h.get("reported_return") or 0) * 100.0
        if ret_pct <= threshold_pct:
            _add(
                h["name"],
                ret_pct,
                h.get("exit_date"),
                h.get("start_price"),
                h.get("end_price"),
            )

    if period_analysis:
        for h in period_analysis.get("portfolio_holdings_comparison") or []:
            sym = (h.get("symbol") or "").upper().strip()
            ch = h.get("change") or {}
            st = h.get("start") or {}
            en = h.get("end") or {}
            ret = h.get("price_return_percent_adjusted")
            if ret is None:
                ret = ch.get("price_return_percent_adjusted")
            if ret is None:
                ret = h.get("price_return_percent_unadjusted") or ch.get(
                    "price_return_percent_unadjusted"
                )
            if ret is not None and float(ret) <= threshold_pct:
                exit_raw = h.get("exit_date") or en.get("effective_end_date")
                start_px = float(
                    st.get("current_price") or st.get("price_adjusted") or 0
                ) or None
                end_px = float(
                    en.get("current_price") or en.get("price_adjusted") or 0
                ) or None
                _add(
                    sym,
                    float(ret),
                    parse_date_safe(str(exit_raw)) if exit_raw else None,
                    start_px,
                    end_px,
                )
        for wp in (period_analysis.get("worst_performers") or {}).get(
            "worst_performers"
        ) or []:
            ret = wp.get("price_percent_change")
            if ret is not None and float(ret) <= threshold_pct:
                exit_raw = wp.get("effective_end_date") or wp.get("exit_date")
                _add(
                    wp.get("symbol") or "",
                    float(ret),
                    parse_date_safe(str(exit_raw)) if exit_raw else None,
                    float(wp.get("start_price") or 0) or None,
                    float(wp.get("end_price") or wp.get("current_price") or 0) or None,
                )

    for line in raw.split("\n"):
        if re.search(r"(merged|delisted|suspended)", line, re.IGNORECASE):
            nm = re.search(r"\b([A-Z]{2,15})\b", line)
            if nm:
                _add(nm.group(1), None)

    return list(by_sym.values())


def evaluate_large_loss_with_corporate_actions(
    symbol: str,
    reported_ret: Optional[float],
    period_start: Optional[date],
    period_end: Optional[date],
    exit_date: Optional[date] = None,
    period_analysis: Optional[Dict[str, Any]] = None,
    reported_start_price: Optional[float] = None,
    reported_end_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Classify a ≥40% loser using CorporateAction rows + CA-adjusted prices.

    Returns dict with keys: symbol, verdict ('red'|'amber'|'green'), line, has_ca,
    adjusted_ret (optional).
    """
    sym = (symbol or "").upper().strip()
    security, actions = lookup_corporate_actions_for_symbol(
        sym, period_start, period_end
    )
    ca_summaries = [_format_ca_action_summary(a) for a in actions]
    has_ca = bool(actions)
    demerger_like = any(
        str(getattr(a, "action_type", "") or "").upper() in ("DEMERGER", "MERGER")
        for a in actions
    )

    adj = None
    if security and period_start and period_end:
        adj = compute_adjusted_period_return(
            int(security.id), period_start, period_end, exit_date=exit_date
        )
    if adj is None:
        adj = _pc5_adjusted_from_period_analysis(period_analysis, sym)

    # Prefer CA restatement of the reported table prices when dilutive CAs exist —
    # this catches cases where PriceService adjustment did not apply but CA rows exist.
    restated = None
    if (
        has_ca
        and period_start
        and period_end
        and reported_start_price
        and reported_end_price
    ):
        restated = restate_reported_prices_with_actions(
            float(reported_start_price),
            float(reported_end_price),
            actions,
            period_start,
            period_end,
        )
        if restated and restated.get("restatement_factor", 1.0) != 1.0:
            # Use restated return when PriceService adj is missing or still ≈ reported loss.
            if adj is None:
                adj = restated
            elif reported_ret is not None and adj.get("price_percent_change") is not None:
                if abs(adj["price_percent_change"] - reported_ret) < 5.0:
                    adj = restated

    adj_ret = adj["price_percent_change"] if adj else None
    adj_start = adj["start_price"] if adj else None
    adj_end = adj["end_price"] if adj else None
    reported_s = f"{reported_ret:.2f}%" if reported_ret is not None else "n/a"
    adj_s = (
        f"{adj_ret:.2f}% (₹{adj_start:,.2f} → ₹{adj_end:,.2f})"
        if adj_ret is not None and adj_start is not None and adj_end is not None
        else "n/a"
    )
    ca_s = "; ".join(ca_summaries) if ca_summaries else "none"

    if has_ca and demerger_like:
        verdict = "amber"
        line = (
            f"{sym}: reported {reported_s} → Corporate action found ({ca_s}). "
            f"Adjusted return {adj_s}. Demerger/merger — verify combined return "
            f"including sibling entity before reporting to client."
        )
    elif has_ca and adj_ret is not None and adj_ret > LARGE_LOSS_PCT:
        verdict = "amber"
        line = (
            f"{sym}: reported {reported_s} → Corporate action found ({ca_s}). "
            f"Adjusted return {adj_s}. Large reported drop is a CA price-basis artefact, "
            f"not an economic loss of that magnitude."
        )
    elif has_ca and adj_ret is not None and adj_ret <= LARGE_LOSS_PCT:
        verdict = "amber"
        line = (
            f"{sym}: reported {reported_s} → Corporate action found ({ca_s}). "
            f"Adjusted return still {adj_s} (≤{LARGE_LOSS_PCT:g}%). "
            f"Treat as real loss after CA adjustment — investigate fundamentals."
        )
    elif has_ca:
        verdict = "amber"
        line = (
            f"{sym}: reported {reported_s} → Corporate action found ({ca_s}). "
            f"Could not resolve adjusted prices — verify CA-adjusted return before "
            f"citing this loss to the client."
        )
    elif adj_ret is not None and adj_ret > LARGE_LOSS_PCT:
        verdict = "amber"
        line = (
            f"{sym}: reported {reported_s} → No corporate_actions row in period, but "
            f"adjusted return {adj_s}. Check missing CA seed vs price-source basis."
        )
    else:
        verdict = "red"
        line = (
            f"{sym}: reported {reported_s} → No corporate action in corporate_actions "
            f"for this period. Adjusted return {adj_s}. "
            f"Investigate — real loss or missing CA / unadjusted prices?"
        )

    return {
        "symbol": sym,
        "verdict": verdict,
        "line": line,
        "has_ca": has_ca,
        "adjusted_ret": adj_ret,
        "actions": actions,
    }


def verdict_from_diff(diff_pct: float) -> str:
    if diff_pct <= PRICE_TOL_PCT:
        return "green"
    if diff_pct <= PRICE_ERR_PCT:
        return "amber"
    return "red"


def emoji(v: str) -> str:
    return {"green": "🟢", "amber": "🟡", "red": "🔴", "info": "ℹ️", "skipped": "⏭️"}.get(v, "❓")


def run_price_audit(req: PriceAuditRequest) -> AuditResponse:
    enabled = {k: True for k in ["pc1", "pc2", "pc3", "pc4", "pc5", "pc6"]}
    enabled.update(req.checks)

    raw = req.raw_data
    results: List[CheckResult] = []
    notes_out: List[str] = []
    period_start = parse_date_safe(req.period_start) if req.period_start else None
    period_end = parse_date_safe(req.period_end) if req.period_end else None
    missing_prices: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    missing_ranges: Dict[Tuple[str, date, date], Set[str]] = defaultdict(set)

    if enabled.get("pc1", True):
        arith_lines = []
        verdicts = []
        for label, dt in [("start", period_start), ("end", period_end)]:
            reported_val = find_first(
                rf"nifty.*?(?:{label}|{'open' if label == 'start' else 'close'}).*?([\d,]+\.?\d*)",
                raw,
            )
            if not reported_val:
                reported_val = find_first(r"nifty.*?([\d,]+\.?\d*)", raw)
            if dt:
                live_price, src_date = fetch_closing_price("^NSEI", dt)
                if live_price:
                    rep_f = float(reported_val.replace(",", "")) if reported_val else None
                    if rep_f:
                        diff = abs(live_price - rep_f) / live_price * 100
                        v = verdict_from_diff(diff)
                        arith_lines.append(
                            f"Nifty {label} ({dt}): reported ={rep_f:,.0f}  |  live ({src_date}) ={live_price:,.0f}  |  diff ={diff:.2f}%  → {v.upper()}"
                        )
                        verdicts.append(v)
                    else:
                        arith_lines.append(f"Nifty {label} ({dt}): live ={live_price:,.0f} (no reported value to compare)")
                        verdicts.append("amber")
                else:
                    arith_lines.append(f"Nifty {label}: could not resolve closing price for {dt}")
                    verdicts.append("amber")
                    _record_price_miss(missing_prices, "NIFTY", dt, f"pc1_nifty_{label}_close ({src_date})")
            else:
                arith_lines.append(f"Period {label} date not provided.")
                verdicts.append("amber")
        v_final = min(verdicts, key=lambda x: VERDICT_ORDER[x]) if verdicts else "amber"
        results.append(
            CheckResult(
                id="pc1",
                label="Nifty 50 — period start & end prices",
                verdict=v_final,
                emoji=emoji(v_final),
                detail=(
                    "Live Nifty levels from database / Google Sheets compared to reported benchmark prices."
                    if v_final != "amber"
                    else "Could not fully verify Nifty levels — check benchmark_data and Google Sheets sync."
                ),
                arithmetic="\n".join(arith_lines),
            )
        )

    if enabled.get("pc2", True):
        holdings = extract_holdings(raw)
        exit_dates_by_symbol = _pc2_exit_dates_by_symbol(req.period_analysis)
        holder_meta_by_symbol = _pc2_holder_meta_by_symbol(req.period_analysis)
        arith_lines, red_items, amber_items = [], [], []
        entry_start_count = 0
        for h in holdings:
            sp, ep = h["start_price"], h["end_price"]
            sym_key = (h.get("name") or "").upper().strip()
            meta = dict(holder_meta_by_symbol.get(sym_key) or {})
            return_basis = h.get("return_basis") or meta.get("return_basis") or "period_start_market"
            meta["return_basis"] = return_basis
            end_verify_date = (
                h.get("exit_date")
                or exit_dates_by_symbol.get(sym_key)
                or period_end
            )
            end_label = "end (exit)" if end_verify_date and end_verify_date != period_end else "end"

            if _pc2_uses_entry_start_price(return_basis):
                entry_start_count += 1
                _pc2_verify_entry_start_price(
                    h["name"], sp, meta, arith_lines, red_items, amber_items
                )
            elif period_start:
                live_p, src_date = fetch_closing_price(h["name"], period_start)
                if live_p:
                    diff = abs(live_p - sp) / live_p * 100
                    v = verdict_from_diff(diff)
                    arith_lines.append(
                        f"{h['name']} start @ {period_start}: reported={sp:,.2f}  live={live_p:,.2f} ({src_date})  diff={diff:.2f}%  → {v.upper()}"
                    )
                    if v == "red":
                        red_items.append(f"{h['name']} start ({diff:.1f}% off)")
                    elif v == "amber":
                        amber_items.append(f"{h['name']} start ({diff:.1f}% off)")
                else:
                    arith_lines.append(f"{h['name']} start @ {period_start}: could not resolve closing price")
                    amber_items.append(f"{h['name']} start (fetch failed)")
                    _record_price_miss(
                        missing_prices,
                        h["name"],
                        period_start,
                        f"pc2_holding_start_close ({src_date})",
                    )

            if end_verify_date:
                live_p, src_date = fetch_closing_price(h["name"], end_verify_date)
                if live_p:
                    diff = abs(live_p - ep) / live_p * 100
                    v = verdict_from_diff(diff)
                    arith_lines.append(
                        f"{h['name']} {end_label} @ {end_verify_date}: reported={ep:,.2f}  live={live_p:,.2f} ({src_date})  diff={diff:.2f}%  → {v.upper()}"
                    )
                    if v == "red":
                        red_items.append(f"{h['name']} {end_label} ({diff:.1f}% off)")
                    elif v == "amber":
                        amber_items.append(f"{h['name']} {end_label} ({diff:.1f}% off)")
                else:
                    arith_lines.append(
                        f"{h['name']} {end_label} @ {end_verify_date}: could not resolve closing price"
                    )
                    amber_items.append(f"{h['name']} {end_label} (fetch failed)")
                    _record_price_miss(
                        missing_prices,
                        h["name"],
                        end_verify_date,
                        f"pc2_holding_{end_label}_close ({src_date})",
                    )

            comp_ret = (ep - sp) / sp * 100 if sp > 0 else None
            rep_ret = h["reported_return"] * 100
            if comp_ret is not None:
                ret_diff = abs(comp_ret - rep_ret)
                arith_lines.append(
                    f"{h['name']} return: ({ep}-{sp})/{sp} = {comp_ret:.2f}%  reported={rep_ret:.2f}%  diff={ret_diff:.2f}%"
                )
                if ret_diff > CALC_ERR_PCT:
                    amber_items.append(f"{h['name']} return % ({ret_diff:.1f}% off)")
        v_final = "red" if red_items else ("amber" if amber_items or not holdings else "green")
        exited_count = sum(
            1
            for h in holdings
            if (h.get("exit_date") or exit_dates_by_symbol.get((h.get("name") or "").upper().strip()))
        )
        detail = (
            f"Verified {len(holdings)} holding(s) against market data closes. "
            + (
                f"{entry_start_count} with entry/blended start (not checked vs period-start close). "
                if entry_start_count
                else ""
            )
            + (f"{exited_count} exited — end price checked at sale/exit date, not period end. " if exited_count else "")
            + (f"🔴 Red: {'; '.join(red_items)}. " if red_items else "")
            + (f"🟡 Amber: {'; '.join(amber_items)}." if amber_items else "")
            + ("✅ All prices match." if not red_items and not amber_items and holdings else "")
            + ("" if holdings else "No holdings table detected.")
        )
        results.append(
            CheckResult(
                id="pc2",
                label="Holdings start & end prices — market data verification",
                verdict=v_final,
                emoji=emoji(v_final),
                reported=f"{len(holdings)} holdings",
                detail=detail,
                arithmetic="\n".join(arith_lines),
            )
        )

    if enabled.get("pc3", True):
        buy_pat = re.compile(r"\b([A-Z]{2,15})\b.*?([\d,]+\.?\d*)\s*(?:avg|average)?\s*buy", re.IGNORECASE)
        trades = [(m.group(1), float(m.group(2).replace(",", ""))) for m in buy_pat.finditer(raw)]
        arith_lines, red_items, amber_items = [], [], []
        for name, avg_buy in trades:
            if period_start and period_end:
                low, high = fetch_price_range(name, period_start, period_end)
                if low is not None and high is not None:
                    in_range = low <= avg_buy <= high
                    v = "green" if in_range else "red"
                    arith_lines.append(
                        f"{name}: avg buy={avg_buy:,.2f}  period range=[{low:,.2f}–{high:,.2f}]  → {'IN RANGE ✅' if in_range else 'OUT OF RANGE ❌'}"
                    )
                    if not in_range:
                        red_items.append(f"{name} (buy={avg_buy:,.0f}, range={low:,.0f}–{high:,.0f})")
                else:
                    arith_lines.append(f"{name}: could not fetch period low/high from database (historical_price)")
                    amber_items.append(f"{name} (fetch failed)")
                    _record_price_range_gap(
                        missing_ranges,
                        name,
                        period_start,
                        period_end,
                        f"pc3 period OHLC [{period_start.isoformat()} .. {period_end.isoformat()}] (historical_price)",
                    )
        v_final = "red" if red_items else ("amber" if amber_items else ("green" if trades else "amber"))
        detail = (
            f"Checked {len(trades)} traded stock(s) avg buy prices against period low/high from market data. "
            + (f"🔴 Out of range: {'; '.join(red_items)}. " if red_items else "")
            + (f"🟡 Unverified: {'; '.join(amber_items)}. " if amber_items else "")
            + ("✅ All avg buy prices within period ranges." if not red_items and not amber_items and trades else "")
            + ("" if trades else "No 'avg buy' trades found in data.")
        )
        results.append(
            CheckResult(
                id="pc3",
                label="Trade avg buy prices — within period NSE range",
                verdict=v_final,
                emoji=emoji(v_final),
                detail=detail,
                arithmetic="\n".join(arith_lines) if arith_lines else None,
            )
        )

    if enabled.get("pc4", True):
        arith_lines = []
        symbols: Set[str] = {h["name"] for h in extract_holdings(raw)}
        if req.period_analysis:
            for h in req.period_analysis.get("portfolio_holdings_comparison") or []:
                sym = (h.get("symbol") or "").upper().strip()
                if sym:
                    symbols.add(sym)
            for wp in (req.period_analysis.get("worst_performers") or {}).get(
                "worst_performers"
            ) or []:
                sym = (wp.get("symbol") or "").upper().strip()
                if sym:
                    symbols.add(sym)

        # Keep legacy text patterns for free-form adviser notes.
        split_pat = re.compile(
            r"\b([A-Z]{2,15})\b.*?(\d+)\s*:\s*(\d+)\s*split", re.IGNORECASE
        )
        bonus_pat = re.compile(
            r"\b([A-Z]{2,15})\b.*?(\d+)\s*:\s*(\d+)\s*bonus", re.IGNORECASE
        )
        for m in split_pat.finditer(raw):
            stock, n, d = m.group(1), int(m.group(2)), int(m.group(3))
            factor = n / d if d else 0
            arith_lines.append(
                f"{stock} {n}:{d} split (from notes) → price ÷{factor:.2f}, qty ×{factor:.2f}"
            )
        for m in bonus_pat.finditer(raw):
            stock, n, d = m.group(1), int(m.group(2)), int(m.group(3))
            factor = (n + d) / d if d else 0
            arith_lines.append(
                f"{stock} {n}:{d} bonus (from notes) → price ÷{factor:.2f}, qty ×{factor:.2f}"
            )

        db_action_count = 0
        for sym in sorted(symbols):
            _sec, actions = lookup_corporate_actions_for_symbol(
                sym, period_start, period_end
            )
            for action in actions:
                db_action_count += 1
                action_type = str(getattr(action, "action_type", "") or "").upper()
                ratio = float(getattr(action, "ratio", 0) or 0)
                summary = _format_ca_action_summary(action)
                if action_type == "SPLIT" and ratio:
                    arith_lines.append(
                        f"{sym} {summary} → price ÷{ratio:g}, qty ×{ratio:g} "
                        f"[restated to current share basis via corporate_actions]"
                    )
                elif action_type == "BONUS" and ratio:
                    factor = 1.0 + ratio
                    arith_lines.append(
                        f"{sym} {summary} → price ÷{factor:g}, qty ×{factor:g} "
                        f"[restated to current share basis via corporate_actions]"
                    )
                else:
                    arith_lines.append(
                        f"{sym} {summary} → verify quantity/cost allocation "
                        f"(from corporate_actions)"
                    )

        if db_action_count:
            v_final = "green"
            detail = (
                f"Found {db_action_count} corporate action(s) in corporate_actions "
                f"for holdings in this period."
            )
        elif arith_lines:
            v_final = "amber"
            detail = (
                "Corporate actions mentioned in notes only — "
                "confirm they exist in corporate_actions."
            )
        else:
            v_final = "info"
            detail = "No corporate actions (splits/bonuses/demergers) in period for holdings."
        results.append(
            CheckResult(
                id="pc4",
                label="Corporate action adjustments",
                verdict=v_final,
                emoji=emoji(v_final),
                detail=detail,
                arithmetic="\n".join(arith_lines) if arith_lines else None,
            )
        )

    if enabled.get("pc5", True):
        large_losers = collect_large_loss_symbols(raw, req.period_analysis)
        arith_lines, red_items, amber_items = [], [], []
        for loser in large_losers:
            outcome = evaluate_large_loss_with_corporate_actions(
                loser["name"],
                loser.get("ret"),
                period_start,
                period_end,
                exit_date=loser.get("exit_date"),
                period_analysis=req.period_analysis,
                reported_start_price=loser.get("start_price"),
                reported_end_price=loser.get("end_price"),
            )
            arith_lines.append(outcome["line"])
            if outcome["verdict"] == "red":
                red_items.append(outcome["symbol"])
            elif outcome["verdict"] == "amber":
                amber_items.append(outcome["symbol"])
        v_final = "red" if red_items else ("amber" if amber_items else "green")
        detail = (
            f"Found {len(large_losers)} stock(s) with ≥40% reported loss or delisting. "
            + (
                f"🔴 No CA / still unexplained: {', '.join(red_items)}. "
                if red_items
                else ""
            )
            + (
                f"🟡 CA found or adjusted basis differs: {', '.join(amber_items)}. "
                if amber_items
                else ""
            )
            + ("✅ No large unexplained losses." if not large_losers else "")
        )
        results.append(
            CheckResult(
                id="pc5",
                label="Corporate-action artefact check (≥40% losses)",
                verdict=v_final,
                emoji=emoji(v_final),
                detail=detail,
                arithmetic="\n".join(arith_lines) if arith_lines else None,
            )
        )

    if enabled.get("pc6", True):
        stale, arith_lines, skipped_exited = [], [], []
        if period_end:
            exit_dates_by_symbol = _pc2_exit_dates_by_symbol(req.period_analysis)
            for h in extract_holdings(raw):
                sym_key = (h.get("name") or "").upper().strip()
                if sym_key and h.get("exit_date"):
                    exit_dates_by_symbol[sym_key] = h["exit_date"]
            exited_symbols = set(exit_dates_by_symbol.keys())

            date_pat = re.compile(
                r"\b([A-Z]{2,15})\b.*?(\d{1,2}[-/\s][A-Za-z]{3}[-/\s]\d{4}|\d{4}[-/]\d{2}[-/]\d{2})"
            )
            for m in date_pat.finditer(raw):
                sym = (m.group(1) or "").upper().strip()
                d = parse_date_safe(m.group(2))
                if not d or d >= period_end:
                    continue
                line_start = raw.rfind("\n", 0, m.start()) + 1
                line_end = raw.find("\n", m.start())
                line = raw[line_start : line_end if line_end != -1 else len(raw)]
                # Holdings sold during period: end price is at exit=DATE, not period end — not stale.
                if sym in exited_symbols:
                    exit_dt = exit_dates_by_symbol.get(sym)
                    if exit_dt and d == exit_dt:
                        if sym not in skipped_exited:
                            skipped_exited.append(sym)
                            arith_lines.append(
                                f"{sym}: skipped (exited {exit_dt} — end price not expected at period end {period_end})"
                            )
                        continue
                    if re.search(rf"\bexit\s*=\s*{re.escape(d.isoformat())}\b", line, re.IGNORECASE):
                        if sym not in skipped_exited:
                            skipped_exited.append(sym)
                            arith_lines.append(
                                f"{sym}: skipped (exit={d} on holdings line — not period-end price)"
                            )
                        continue
                    # Any other date for an exited symbol in audit text is not a period-end quote.
                    continue
                bdays = business_days_between(d, period_end)
                if bdays > STALE_DAYS:
                    stale.append({"stock": sym, "price_date": str(d), "bdays": bdays})
                    arith_lines.append(
                        f"{sym}: price date {d} is {bdays} business days before period end {period_end}"
                    )
            v_final = "red" if stale else "green"
            detail = (
                f"Stale prices detected for: {', '.join(s['stock'] for s in stale[:5])}. "
                f"End prices should be within {STALE_DAYS} business days of {period_end}."
                if stale
                else (
                    f"No stale period-end prices detected (within {STALE_DAYS} business days of {period_end})."
                    + (
                        f" Excluded {len(skipped_exited)} exited holding(s) checked at sale date."
                        if skipped_exited
                        else ""
                    )
                )
            )
        else:
            v_final = "amber"
            detail = "Period end date not provided — cannot check for stale prices."
        results.append(
            CheckResult(
                id="pc6",
                label="Stale price detection",
                verdict=v_final,
                emoji=emoji(v_final),
                detail=detail,
                arithmetic="\n".join(arith_lines) if arith_lines else None,
            )
        )

    if missing_prices or missing_ranges:
        rows, n_display = _build_calc_impacting_price_gap_lines(missing_prices, missing_ranges)
        n_raw = len(missing_prices) + len(missing_ranges)
        results.append(
            CheckResult(
                id="pc_missing_prices",
                label="Price data gaps — symbols & dates (PC1–PC3 only)",
                verdict="amber",
                emoji=emoji("amber"),
                reported=f"{n_display} gap(s) ({n_raw} raw lookup(s))",
                detail=(
                    "Closing prices or period OHLC used by PC1 (Nifty), PC2 (holding start/end), or PC3 (avg buy vs range) "
                    f"were missing in the database / Google Sheets. Listed below after merging consecutive calendar dates into ranges. "
                    f"{n_display} merged line(s). Backfill `historical_price` / `benchmark_data`, sync Sheets, or retry when data exists."
                ),
                arithmetic="SYMBOL           DATE (or inclusive range)  — audit context\n"
                + ("-" * 88 + "\n")
                + ("\n".join(rows) if rows else "(no rows — see raw lookups)"),
            )
        )

    return _build_audit_response(
        "Price Audit", req.client, req.period_start, req.period_end, results, notes_out
    )


def run_calc_audit(req: CalcAuditRequest) -> AuditResponse:
    enabled = {k: True for k in ["cc1", "cc2", "cc3", "cc4", "cc5", "cc6", "cc7", "cc8"]}
    enabled.update(req.checks)

    raw = req.raw_data
    results: List[CheckResult] = []
    notes_out: List[str] = []
    period_start = parse_date_safe(req.period_start) if req.period_start else None
    period_end = parse_date_safe(req.period_end) if req.period_end else None
    period_months = None
    if period_start and period_end:
        delta = relativedelta(period_end, period_start)
        period_months = delta.years * 12 + delta.months + delta.days / 30.0

    if enabled.get("cc1", True):
        cfs = extract_cashflows(raw)
        reported_xirr = find_first(r"portfolio\s+xirr[:\s]+([-+]?[\d.]+)%", raw)
        if not reported_xirr:
            reported_xirr = find_first(r"\bxirr[:\s]+([-+]?[\d.]+)%", raw)
        computed = None
        arith = None
        if len(cfs) >= 2:
            computed = xirr(cfs)
            arith = "\n".join(f"{str(cf[0])}: ₹{cf[1]:,.0f}" for cf in cfs[:10])
            if computed is not None and reported_xirr:
                r_val = float(reported_xirr) / 100
                diff = abs(computed - r_val) * 100
                v = "green" if diff <= CALC_TOL_PCT else ("amber" if diff <= CALC_ERR_PCT else "red")
                detail = (
                    f"Reported XIRR {reported_xirr}% vs computed {computed * 100:.2f}% from {len(cfs)} cashflows. "
                    f"Difference: {diff:.2f}%. {'✅ Match.' if v == 'green' else '⚠️ Discrepancy — verify cashflow dates/amounts.'}"
                )
            elif computed is not None:
                v = "amber"
                detail = f"Computed XIRR: {computed * 100:.2f}% from {len(cfs)} cashflows. No reported XIRR found to compare."
            else:
                v = "amber"
                detail = "XIRR solver did not converge. Cashflows may be inconsistent."
                arith = None
        else:
            v = "amber"
            detail = f"Only {len(cfs)} cashflow(s) extracted — need ≥2 for XIRR. Include cashflow table in data."
            arith = None
        results.append(
            CheckResult(
                id="cc1",
                label="Portfolio XIRR",
                verdict=v,
                emoji=emoji(v),
                reported=reported_xirr + "%" if reported_xirr else "Not found",
                computed=f"{computed * 100:.2f}%" if computed is not None else "N/A",
                detail=detail,
                arithmetic=arith,
            )
        )

    if enabled.get("cc2", True):
        eq_xirr = find_first(r"equity\s+xirr[:\s]+([-+]?[\d.]+)%", raw)
        fi_xirr = find_first(r"(?:fi|fixed\s*income|debt)\s+xirr[:\s]+([-+]?[\d.]+)%", raw)
        eq_inv = find_first(r"equity\s+invested[:\s]+([\d.]+[Ll]?)", raw)
        fi_inv = find_first(r"(?:fi|fixed\s*income|debt)\s+invested[:\s]+([\d.]+[Ll]?)", raw)
        eq_val = find_first(r"equity\s+(?:current|value)[:\s]+([\d.]+[Ll]?)", raw)
        fi_val = find_first(r"(?:fi|fixed\s*income|debt)\s+(?:current|value)[:\s]+([\d.]+[Ll]?)", raw)
        arith_lines, issues = [], []
        if eq_xirr and eq_inv and eq_val:
            eq_x_f = float(eq_xirr) / 100
            eq_i_f = parse_inr(eq_inv) or 0
            eq_v_f = parse_inr(eq_val) or 0
            arith_lines.append(
                f"Equity: invested ₹{eq_i_f / 1e5:.2f}L → value ₹{eq_v_f / 1e5:.2f}L | XIRR {eq_xirr}%"
            )
            if eq_x_f < 0 and eq_v_f > eq_i_f:
                issues.append(
                    f"Equity XIRR is {eq_xirr}% (negative) but current value > invested — inconsistency."
                )
            if eq_x_f > 0 and eq_v_f < eq_i_f:
                issues.append(
                    f"Equity XIRR is {eq_xirr}% (positive) but current value < invested — inconsistency."
                )
        if fi_xirr and fi_inv:
            fi_x_f = float(fi_xirr) / 100
            fi_i_f = parse_inr(fi_inv) or 0
            arith_lines.append(f"FI: invested ₹{fi_i_f / 1e5:.2f}L | XIRR {fi_xirr}%")
            if fi_x_f < 0:
                issues.append(f"Fixed income XIRR is negative ({fi_xirr}%) — unusual, verify.")
            if fi_x_f > 0.20:
                issues.append(f"FI XIRR {fi_xirr}% seems high — verify.")
        found = bool(eq_xirr or fi_xirr)
        v = "red" if issues else ("green" if found else "amber")
        detail = (
            f"Eq XIRR: {eq_xirr + '%' if eq_xirr else 'not found'}. FI XIRR: {fi_xirr + '%' if fi_xirr else 'not found'}. "
            + (f"Issues: {'; '.join(issues)}" if issues else ("Directionally consistent. ✅" if found else "Segment data not found."))
        )
        results.append(
            CheckResult(
                id="cc2",
                label="Segment XIRR (Equity + FI)",
                verdict=v,
                emoji=emoji(v),
                reported=f"Eq:{eq_xirr}% FI:{fi_xirr}%" if eq_xirr or fi_xirr else "Not found",
                detail=detail,
                arithmetic="\n".join(arith_lines) if arith_lines else None,
            )
        )

    if enabled.get("cc3", True):
        bmk_xirr = find_first(r"benchmark\s+xirr[:\s]+([-+]?[\d.]+)%", raw)
        bmk_units = find_first(r"accumulated\s+units?[:\s]+([\d.]+)", raw)
        bmk_epx = find_first(r"nifty.*?end.*?price[:\s]+([\d,]+\.?\d*)", raw)
        bmk_fval = find_first(r"benchmark.*?(?:final|end|current)\s+value[:\s]+([\d.]+[Ll]?)", raw)
        arith, v, detail = None, "amber", ""
        if bmk_units and bmk_epx and bmk_fval:
            computed_val = float(bmk_units) * float(bmk_epx.replace(",", ""))
            reported_val = parse_inr(bmk_fval)
            if reported_val and reported_val > 0:
                diff_pct = abs(computed_val - reported_val) / reported_val * 100
                v = "green" if diff_pct <= CALC_TOL_PCT else ("amber" if diff_pct <= 2 else "red")
                arith = (
                    f"{bmk_units} units × ₹{bmk_epx} = ₹{computed_val:,.0f}  |  reported ₹{reported_val:,.0f}  |  diff {diff_pct:.2f}%"
                )
                detail = (
                    f"Accumulated units × end price = ₹{computed_val / 1e5:.2f}L vs reported ₹{reported_val / 1e5:.2f}L. Diff: {diff_pct:.2f}%. "
                    f"{'✅' if v == 'green' else '⚠️'}"
                )
            else:
                detail = "Could not parse benchmark final value."
        elif bmk_xirr:
            detail = (
                f"Benchmark XIRR {bmk_xirr}% found but benchmark table (units × price) not structured. Include benchmark cashflow table."
            )
        else:
            detail = "Benchmark XIRR and cashflow table not found. Include benchmark verification section."
        results.append(
            CheckResult(
                id="cc3",
                label="Benchmark XIRR",
                verdict=v,
                emoji=emoji(v),
                reported=bmk_xirr + "%" if bmk_xirr else "Not found",
                detail=detail,
                arithmetic=arith,
            )
        )

    if enabled.get("cc4", True):
        twr_reported = find_first(r"\btwr[:\s]+([-+]?[\d.]+)%", raw)
        sub_pats = re.findall(r"(?:sub.?period|period)\s*\d+[:\s]+([-+]?[\d.]+)%", raw, re.IGNORECASE)
        sub_returns = [float(s) / 100 for s in sub_pats]
        computed_twr = None
        arith = None
        if sub_returns and twr_reported:
            computed_twr = twr_chain(sub_returns)
            reported_twr = float(twr_reported) / 100
            diff = abs(computed_twr - reported_twr) * 100
            v = "green" if diff <= CALC_TOL_PCT else ("amber" if diff <= CALC_ERR_PCT else "red")
            chain_str = " × ".join(f"(1{r * 100:+.2f}%)" for r in sub_returns)
            arith = (
                f"{chain_str}\n= {' × '.join(str(round(1 + r, 4)) for r in sub_returns)}\n"
                f"= {1 + computed_twr:.4f} → TWR = {computed_twr * 100:.2f}%\n"
                f"Reported TWR = {twr_reported}%  |  Diff = {diff:.2f}%"
            )
            detail = (
                f"Chain multiply of {len(sub_returns)} sub-periods = {computed_twr * 100:.2f}% vs reported {twr_reported}%. "
                f"{'✅ Match.' if v == 'green' else f'⚠️ Diff {diff:.2f}% — verify sub-period returns.'}"
            )
        elif twr_reported:
            v = "amber"
            detail = f"TWR {twr_reported}% found but no sub-period breakdown. Include sub-period return rows."
            arith = None
        else:
            v = "amber"
            detail = "TWR not found. Include TWR figure and sub-period returns."
            arith = None
        results.append(
            CheckResult(
                id="cc4",
                label="TWR sub-period chain multiply",
                verdict=v,
                emoji=emoji(v),
                reported=twr_reported + "%" if twr_reported else "Not found",
                computed=f"{computed_twr * 100:.2f}%" if computed_twr is not None else "N/A",
                detail=detail,
                arithmetic=arith,
            )
        )

    if enabled.get("cc5", True):
        pa = req.period_analysis or {}
        per = pa.get("period") or {}
        period_audit = (
            f"{per.get('start_date') or (req.period_start or '').strip() or '?'}"
            f" → {per.get('end_date') or (req.period_end or '').strip() or '?'}"
        )
        mtm = pa.get("mark_to_market_gains") or {}
        psv = float(mtm["total_start_value"]) if mtm.get("total_start_value") is not None else None
        pev = float(mtm["total_end_value"]) if mtm.get("total_end_value") is not None else None
        portfolio_ctx = ""
        if psv is not None:
            portfolio_ctx += f"Start value ₹{psv / 1e5:.2f}L. "
        if pev is not None:
            portfolio_ctx += f"End value ₹{pev / 1e5:.2f}L. "

        def _float_or_none(x) -> Optional[float]:
            if x is None:
                return None
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        bg = cg = nf = tc = None
        pathway = pa.get("pathway") or {}
        comps = pathway.get("pathway_components") or {}
        if comps or pathway.get("total_change") is not None:
            bg = _float_or_none((comps.get("base_portfolio_gains") or {}).get("value"))
            cg = _float_or_none((comps.get("gains_from_changes") or {}).get("value"))
            nf = _float_or_none((comps.get("new_funds_added") or {}).get("value"))
            tc = _float_or_none(pathway.get("total_change"))

        base_gains = find_first(r"base.*?gains?[:\s]+([-+]?[\d.]+[Ll]?)", raw)
        change_gains = find_first(r"(?:change|changes)\s+gains?[:\s]+([-+]?[\d.]+[Ll]?)", raw)
        if change_gains is None:
            change_gains = find_first(r"new\s+positions?\s+gains?[:\s]+([-+]?[\d.]+[Ll]?)", raw)
        if change_gains is None:
            change_gains = find_first(r"(?:changes?|new\s+positions?)\s+gains?[:\s]+([-+]?[\d.]+[Ll]?)", raw)
        new_funds = find_first(r"new\s+funds?[:\s]+([-+]?[\d.]+[Ll]?)", raw)
        total_change = find_first(r"total\s+change[:\s]+([-+]?[\d.]+[Ll]?)", raw)
        if bg is None and base_gains:
            bg = parse_inr(base_gains)
        if cg is None and change_gains:
            cg = parse_inr(change_gains)
        if nf is None and new_funds:
            nf = parse_inr(new_funds)
        if tc is None and total_change:
            tc = parse_inr(total_change)

        arith, v, detail = None, "amber", ""
        if bg is not None and tc is not None:
            cg_f = float(cg) if cg is not None else 0.0
            nf_f = float(nf) if nf is not None else 0.0
            tc_f = float(tc)
            comp_total = float(bg) + cg_f + nf_f
            diff = abs(comp_total - tc_f)
            v = "green" if diff < 1000 else ("amber" if diff < 50000 else "red")
            arith = (
                f"Audit period: {period_audit}\n"
                + (f"Portfolio MTM: start ₹{psv / 1e5:.2f}L, end ₹{pev / 1e5:.2f}L\n" if psv is not None and pev is not None else "")
                + f"Base gains:   ₹{float(bg) / 1e5:.2f}L\n"
                f"Change gains: ₹{cg_f / 1e5:.2f}L\n"
                f"New funds:    ₹{nf_f / 1e5:.2f}L\n"
                f"─────────────────────────\n"
                f"Computed:     ₹{comp_total / 1e5:.2f}L\n"
                f"Reported:     ₹{tc_f / 1e5:.2f}L\n"
                f"Difference:   ₹{diff:,.0f}"
            )
            detail = (
                f"Period: {period_audit}. {portfolio_ctx}"
                f"Pathway: ₹{float(bg) / 1e5:.2f}L + ₹{cg_f / 1e5:.2f}L + ₹{nf_f / 1e5:.2f}L = ₹{comp_total / 1e5:.2f}L vs total change ₹{tc_f / 1e5:.2f}L. "
                f"Diff ₹{diff:,.0f}. {'✅ Balances.' if v == 'green' else '⚠️ Does not balance.'}"
            )
        else:
            detail = (
                f"Period: {period_audit}. {portfolio_ctx}"
                "Could not resolve pathway figures (base / change / new funds / total change) from period JSON or audit text. "
                "Ensure pathway_components and pathway.total_change exist in period analysis."
            )
        results.append(
            CheckResult(
                id="cc5",
                label="Pathway cross-check (Base + Changes + New funds = Total change)",
                verdict=v,
                emoji=emoji(v),
                detail=detail,
                arithmetic=arith,
            )
        )

    if enabled.get("cc6", True):
        abs_ret_str = find_first(r"(?:absolute|lifetime|total)\s+return[:\s]+([\d.]+)%", raw)
        total_profit = find_first(r"total\s+profit[:\s]+([\d.]+[Ll]?)", raw)
        total_inv = find_first(r"total\s+invested[:\s]+([\d.]+[Ll]?)", raw)
        arith, v, detail = None, "amber", ""
        comp_abs = None
        if total_profit and total_inv:
            tp = parse_inr(total_profit)
            ti = parse_inr(total_inv)
            if tp and ti and ti > 0:
                comp_abs = tp / ti * 100
                arith = f"₹{tp / 1e5:.2f}L ÷ ₹{ti / 1e5:.2f}L = {comp_abs:.2f}%"
                if abs_ret_str:
                    diff = abs(comp_abs - float(abs_ret_str))
                    v = "green" if diff <= 0.5 else ("amber" if diff <= 2 else "red")
                    detail = (
                        f"Lifetime return = Total Profit ÷ Total Invested = {comp_abs:.2f}% vs reported {abs_ret_str}%. "
                        f"Diff: {diff:.2f}%. {'✅ Match. Correctly labelled as lifetime return.' if v == 'green' else '⚠️ Discrepancy.'}"
                    )
                else:
                    v = "amber"
                    detail = f"Computed lifetime absolute return: {comp_abs:.2f}%. No reported figure found to compare against."
            else:
                detail = "Could not parse profit/invested amounts."
        else:
            detail = "Total Profit and Total Invested fields not found. Needed for lifetime absolute return verification."
        results.append(
            CheckResult(
                id="cc6",
                label="Absolute return — lifetime label verification",
                verdict=v,
                emoji=emoji(v),
                reported=abs_ret_str + "%" if abs_ret_str else "Not found",
                computed=f"{comp_abs:.2f}%" if comp_abs is not None else "N/A",
                detail=detail,
                arithmetic=arith,
            )
        )

    if enabled.get("cc7", True):
        avg_dur_str = find_first(r"avg.*?duration[:\s]+([\d.]+)\s*months?", raw)
        new_pos_count = find_first(r"(\d+)\s+new\s+positions?", raw)
        arith, v, detail = None, "amber", ""
        if avg_dur_str and period_months:
            dur_f = float(avg_dur_str)
            diff = abs(dur_f - period_months)
            n_pos = int(new_pos_count) if new_pos_count else 0
            arith = (
                f"Period length: {period_months:.1f} months\n"
                f"Reported avg duration: {dur_f} months\n"
                f"New positions opened: {n_pos}\n"
                f"Expected: {'< period length (new positions reduce avg)' if n_pos > 0 else '≈ period length (all positions full period)'}"
            )
            if n_pos > 0:
                v = "green" if dur_f < period_months else "amber"
                detail = (
                    f"Period {period_months:.1f}m, avg duration {dur_f}m, {n_pos} new positions. "
                    f"{'✅ Consistent — new positions lower avg duration.' if v == 'green' else '⚠️ Avg duration ≥ period despite new positions.'}"
                )
            else:
                v = "green" if diff <= 0.5 else "amber"
                detail = (
                    f"Period {period_months:.1f}m, avg duration {dur_f}m. "
                    f"{'✅ Consistent.' if v == 'green' else '⚠️ Duration differs from period — check for mid-period inflows.'}"
                )
        elif avg_dur_str:
            detail = f"Avg duration {avg_dur_str}m found but period dates not provided."
        else:
            detail = "Average duration not found in data."
        results.append(
            CheckResult(
                id="cc7",
                label="Average duration consistency",
                verdict=v,
                emoji=emoji(v),
                reported=avg_dur_str + " months" if avg_dur_str else "Not found",
                detail=detail,
                arithmetic=arith,
            )
        )

    if enabled.get("cc8", True):
        port_xirr = find_first(r"portfolio\s+xirr[:\s]+([-+]?[\d.]+)%", raw)
        bmk_xirr_str = find_first(r"benchmark\s+xirr[:\s]+([-+]?[\d.]+)%", raw)
        avg_aum_str = find_first(r"avg.*?(?:portfolio|aum)\s+value[:\s]+([\d.]+[Ll]?)", raw)
        rep_alpha = find_first(r"\balpha[:\s]+([-+]?[\d.]+)%", raw)
        arith, v, detail = None, "amber", ""
        comp_alpha = None
        alpha_inr_str = "—"
        if port_xirr and bmk_xirr_str:
            p_f = float(port_xirr) / 100
            b_f = float(bmk_xirr_str) / 100
            comp_alpha = (p_f - b_f) * 100
            arith_lines = [
                f"Portfolio XIRR {p_f * 100:.2f}% − Benchmark XIRR {b_f * 100:.2f}% = {comp_alpha:+.2f}%"
            ]
            if avg_aum_str:
                apv = parse_inr(avg_aum_str)
                if apv:
                    alpha_inr = comp_alpha / 100 * apv
                    alpha_inr_str = f"₹{alpha_inr:,.0f}"
                    arith_lines.append(f"{comp_alpha:+.2f}% × ₹{apv / 1e5:.2f}L avg AUM = {alpha_inr_str}")
            arith = "\n".join(arith_lines)
            if rep_alpha:
                diff = abs(comp_alpha - float(rep_alpha))
                v = "green" if diff <= CALC_TOL_PCT else "amber"
                detail = (
                    f"Computed alpha: {comp_alpha:+.2f}% vs reported {rep_alpha}%. "
                    f"Diff: {diff:.2f}%. ₹ alpha: {alpha_inr_str}. {'✅' if v == 'green' else '⚠️'}"
                )
            else:
                v = "green" if abs(comp_alpha) < 50 else "amber"
                detail = f"Alpha: {comp_alpha:+.2f}%. ₹ alpha: {alpha_inr_str}."
        else:
            detail = "Portfolio and/or benchmark XIRR not found — alpha cannot be computed."
        results.append(
            CheckResult(
                id="cc8",
                label="Alpha in % and ₹",
                verdict=v,
                emoji=emoji(v),
                reported=rep_alpha + "%" if rep_alpha else "Not found",
                computed=f"{comp_alpha:+.2f}%" if comp_alpha is not None else "N/A",
                detail=detail,
                arithmetic=arith,
            )
        )

    return _build_audit_response(
        "Calculation Audit", req.client, req.period_start, req.period_end, results, notes_out
    )


def check_report_gate(
    price_result: AuditResponse,
    calc_result: AuditResponse,
    overrides: Dict[str, str],
) -> GateCheckResponse:
    blocking: List[Dict[str, str]] = []
    norm_overrides = {str(k): v for k, v in (overrides or {}).items()}
    all_checks = price_result.checks + calc_result.checks
    for check in all_checks:
        if check.verdict == "red":
            if check.id not in norm_overrides:
                blocking.append(
                    {
                        "id": check.id,
                        "label": check.label,
                        "detail": check.detail,
                        "verdict": check.verdict,
                        "phase": "Price Audit" if check.id.startswith("pc") else "Calculation Audit",
                    }
                )
    can_proceed = len(blocking) == 0
    if can_proceed:
        amber_count = sum(1 for c in all_checks if c.verdict == "amber")
        msg = (
            "✅ All RED findings resolved or overridden. Report generation allowed."
            + (f" Note: {amber_count} AMBER flag(s) remain — review before sending to client." if amber_count else "")
        )
    else:
        ids = ", ".join(b["id"] for b in blocking)
        msg = (
            f"🚫 Report blocked. {len(blocking)} unresolved RED finding(s): {ids}.\n\n"
            "To override: supply an `overrides` map with a written reason per check ID."
        )
    return GateCheckResponse(
        can_proceed=can_proceed,
        blocking_findings=blocking,
        override_prompt=msg,
    )


def _build_audit_response(
    phase: str,
    client: str,
    period_start: str,
    period_end: str,
    results: List[CheckResult],
    notes: List[str],
) -> AuditResponse:
    summary = {"green": 0, "amber": 0, "red": 0, "info": 0, "skipped": 0}
    blocking = []
    for r in results:
        summary[r.verdict] = summary.get(r.verdict, 0) + 1
        if r.verdict == "red":
            blocking.append(r.id)
    if summary["red"] > 0:
        overall = f"🔴 {summary['red']} RED finding(s) — report generation blocked until resolved or overridden."
    elif summary["amber"] > 0:
        overall = f"🟡 {summary['amber']} AMBER flag(s) — review before finalising report."
    else:
        overall = "✅ All checks passed — safe to proceed."
    return AuditResponse(
        phase=phase,
        client=client or "Unknown",
        period=f"{period_start} to {period_end}" if period_start and period_end else "Period not specified",
        run_at=datetime.utcnow().isoformat() + "Z",
        summary=summary,
        checks=results,
        overall_verdict=overall,
        blocking_ids=blocking,
        notes=notes,
    )


def run_full_audit_dict(req: FullAuditRequest) -> Dict[str, Any]:
    price_req = PriceAuditRequest(
        client=req.client,
        period_start=req.period_start,
        period_end=req.period_end,
        raw_data=req.raw_data,
        adviser_context=req.adviser_context,
        checks=req.price_checks,
        period_analysis=req.period_analysis,
    )
    calc_req = CalcAuditRequest(
        client=req.client,
        period_start=req.period_start,
        period_end=req.period_end,
        raw_data=req.raw_data,
        checks=req.calc_checks,
        period_analysis=req.period_analysis,
    )
    price_result = run_price_audit(price_req)
    calc_result = run_calc_audit(calc_req)
    gate = check_report_gate(price_result, calc_result, req.overrides)
    return {
        "price_audit": audit_response_to_dict(price_result),
        "calc_audit": audit_response_to_dict(calc_result),
        "gate": gate_response_to_dict(gate),
    }


def _rupees_to_l_label(rupees: float) -> str:
    return f"{rupees / 1e5:.2f}L"


def build_audit_raw_text_from_period_analysis_payload(data: Dict[str, Any]) -> str:
    """Derive audit `raw_data` text from Period Analysis v2 API JSON (Period 1)."""
    lines: List[str] = []
    period = data.get("period") or {}
    if period.get("start_date"):
        lines.append(f"Period start: {period['start_date']}")
    if period.get("end_date"):
        lines.append(f"Period end: {period['end_date']}")
    mtm_hdr = data.get("mark_to_market_gains") or {}
    if mtm_hdr.get("total_start_value") is not None:
        lines.append(f"Portfolio start value: {_rupees_to_l_label(float(mtm_hdr['total_start_value']))}")
    if mtm_hdr.get("total_end_value") is not None:
        lines.append(f"Portfolio end value: {_rupees_to_l_label(float(mtm_hdr['total_end_value']))}")
    bc = data.get("benchmark_comparison") or {}
    if bc.get("benchmark_start_price") is not None:
        lines.append(f"Nifty open on period start: {float(bc['benchmark_start_price']):.2f}")
    if bc.get("benchmark_end_price") is not None:
        lines.append(f"Nifty close on period end: {float(bc['benchmark_end_price']):.2f}")
    px = data.get("period_xirr") or {}
    if px.get("xirr_percent") is not None:
        lines.append(f"Portfolio XIRR: {float(px['xirr_percent']):.2f}%")
    if bc.get("benchmark_xirr_percent") is not None:
        lines.append(f"Benchmark XIRR: {float(bc['benchmark_xirr_percent']):.2f}%")
    twr = data.get("twr") or {}
    if twr.get("twr_percent") is not None:
        lines.append(f"TWR: {float(twr['twr_percent']):.2f}%")
    for i, sp in enumerate(twr.get("sub_period_details") or [], start=1):
        rp = sp.get("return_percent")
        if rp is not None:
            lines.append(f"Sub-period {i}: {float(rp):.2f}%")
    segwrap = data.get("segment_wise_xirr") or {}
    segments = segwrap.get("segments") or {}
    for name, s in segments.items():
        nm = (name or "").lower()
        xp = s.get("xirr_percent")
        if xp is None:
            continue
        if any(k in nm for k in ("equity", "stock", "listed")):
            lines.append(f"Equity XIRR: {float(xp):.2f}%")
            inv = s.get("net_investment") or s.get("total_invested")
            cv = s.get("current_value")
            if inv is not None:
                lines.append(f"Equity invested: {_rupees_to_l_label(float(inv))}")
            if cv is not None:
                lines.append(f"Equity current value: {_rupees_to_l_label(float(cv))}")
        elif any(k in nm for k in ("debt", "fixed", "fi", "bond", "liquid")):
            lines.append(f"FI XIRR: {float(xp):.2f}%")
            inv = s.get("net_investment") or s.get("total_invested")
            if inv is not None:
                lines.append(f"FI invested: {_rupees_to_l_label(float(inv))}")
            if s.get("current_value") is not None:
                lines.append(f"FI current value: {_rupees_to_l_label(float(s['current_value']))}")
    pathway = data.get("pathway") or {}
    comps = (pathway.get("pathway_components") or {}) if pathway else {}
    bg = comps.get("base_portfolio_gains") or {}
    cg = comps.get("gains_from_changes") or {}
    nf = comps.get("new_funds_added") or {}
    if bg.get("value") is not None:
        lines.append(f"Base gains: {_rupees_to_l_label(float(bg['value']))}")
    if cg.get("value") is not None:
        lines.append(f"Change gains: {_rupees_to_l_label(float(cg['value']))}")
    if nf.get("value") is not None:
        lines.append(f"New funds: {_rupees_to_l_label(float(nf['value']))}")
    if pathway.get("total_change") is not None:
        lines.append(f"Total change: {_rupees_to_l_label(float(pathway['total_change']))}")
    if bc.get("benchmark_final_units") is not None:
        lines.append(f"Accumulated units: {float(bc['benchmark_final_units']):.6f}")
    if bc.get("benchmark_end_price") is not None:
        lines.append(f"Nifty end price: {float(bc['benchmark_end_price']):.2f}")
    if bc.get("benchmark_final_value") is not None:
        lines.append(f"Benchmark final value: {_rupees_to_l_label(float(bc['benchmark_final_value']))}")
    if px.get("average_portfolio_value") is not None:
        lines.append(f"Avg portfolio value: {_rupees_to_l_label(float(px['average_portfolio_value']))}")
    if bc.get("outperformance_percent") is not None:
        lines.append(f"Alpha: {float(bc['outperformance_percent']):.2f}%")
    mtm = data.get("mark_to_market_gains") or {}
    invb = data.get("investment_breakdown") or {}
    total_inv = px.get("actual_net_investment") or px.get("net_investment") or invb.get("total_net_investment")
    end_val = px.get("current_value") or mtm.get("total_end_value") or mtm.get("total_current_value")
    if total_inv is not None:
        lines.append(f"Total invested: {_rupees_to_l_label(float(total_inv))}")
    if end_val is not None:
        lines.append(f"Total value: {_rupees_to_l_label(float(end_val))}")
    profit = None
    if total_inv is not None and end_val is not None:
        profit = float(end_val) - float(total_inv)
        lines.append(f"Total profit: {_rupees_to_l_label(profit)}")
    if profit is not None and total_inv and float(total_inv) > 0:
        lines.append(f"Lifetime Absolute Return: {profit / float(total_inv) * 100:.2f}%")
    hc = data.get("holdings_changes") or {}
    new_positions = hc.get("new_positions") or []
    lines.append(f"{len(new_positions)} new positions")
    avg_dur_m = None
    for _name, s in segments.items():
        if s.get("avg_duration_months") is not None:
            avg_dur_m = float(s["avg_duration_months"])
            break
    if avg_dur_m is not None:
        lines.append(f"Average duration: {avg_dur_m:.1f} months")
    lines.append("")
    lines.append("Holdings (SYMBOL  START_PRICE  END_PRICE  RETURN%  [exit=DATE for sold]):")
    for h in data.get("portfolio_holdings_comparison") or []:
        sym = (h.get("symbol") or "?").upper()
        st = h.get("start") or {}
        en = h.get("end") or {}
        sp = float(st.get("price_adjusted") or st.get("current_price") or 0)
        ep = float(en.get("price_adjusted") or en.get("current_price") or 0)
        ret = h.get("price_return_percent_adjusted")
        if ret is None:
            ch = h.get("change") or {}
            ret = ch.get("price_return_percent_adjusted") or 0.0
        start_qty = float(st.get("quantity") or 0)
        end_qty = float(en.get("quantity") or 0)
        exit_suffix = ""
        if start_qty > 0 and end_qty == 0:
            exit_raw = h.get("exit_date") or en.get("effective_end_date") or h.get("effective_end_date")
            if exit_raw:
                exit_suffix = f"  exit={str(exit_raw)[:10]}"
        basis = (h.get("return_basis") or "period_start_market").lower()
        basis_suffix = f"  basis={basis}" if basis != "period_start_market" else ""
        lines.append(f"{sym}  {sp:.2f}  {ep:.2f}  {float(ret):.2f}%{exit_suffix}{basis_suffix}")
    lines.append("")
    lines.append("Cashflows (DATE  AMOUNT):")
    for row in px.get("cashflow_verification") or []:
        ds = row.get("date") or ""
        amt = row.get("amount")
        if ds and amt is not None:
            lines.append(f"{ds}  {float(amt):.0f}")
    return "\n".join(lines)
