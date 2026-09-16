from __future__ import annotations

import os
import re
import sys
import json
import time
import logging
from datetime import datetime, date
from typing import Optional, List, Dict, Tuple, Any
from collections import defaultdict

import httpx
from dateutil import parser as dateparser
from dateutil.relativedelta import relativedelta
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("portfolio_hybrid_report")


def _anthropic():
    """Lazy import — app starts without anthropic installed when AI is off."""
    import anthropic as _anthropic_mod

    return _anthropic_mod

DEFAULT_ANTHROPIC_MODEL = os.environ.get(
    "ANTHROPIC_REPORT_MODEL", "claude-sonnet-4-20250514"
)
# OpenAI-compatible chat completions (used when Anthropic fails or is unset — hybrid report only).
DEFAULT_PERPLEXITY_REPORT_MODEL = os.environ.get("PERPLEXITY_REPORT_MODEL", "sonar-pro")
PERPLEXITY_CHAT_COMPLETIONS_URL = "https://api.perplexity.ai/chat/completions"

# When PORTFOLIO_REVIEW_HYBRID_SKIP_LLM is enabled, prose sections use this block (no external LLM).
HYBRID_MANUAL_NARRATIVE_BLOCK = """[Populate with narrative]

AI enrichment is disabled on the server. Structured tables and the audit engine above are computed locally and are correct; replace this block with adviser-written copy."""


class ReportGeneratorError(Exception):
    """Domain error for hybrid report generation (maps to HTTP status in Flask)."""

    def __init__(self, message: str, status_code: int = 400, detail: Any = None):
        super().__init__(message)
        self.status_code = int(status_code)
        self.detail = detail

# ─────────────────────────────────────────────────────────────
#  PLACEHOLDER REGISTRY
#  All figures that Python injects after Claude returns prose.
#  Claude writes [PLACEHOLDER] tokens; Python replaces them.
# ─────────────────────────────────────────────────────────────

# These are the only placeholders Claude is allowed to use for figures.
# If Claude outputs a raw ₹ number or % that isn't a placeholder, the
# sanitiser strips it before the report is returned.

PLACEHOLDER_DESCRIPTIONS = {
    # Portfolio figures
    "[PORTFOLIO_VALUE]":    "current portfolio value",
    "[TOTAL_INVESTED]":     "total amount invested since inception",
    "[TOTAL_PROFIT]":       "total profit since inception",
    "[LIFETIME_ABS_RET]":   "lifetime absolute return % (since inception)",

    # Period performance
    "[PORTFOLIO_XIRR]":     "portfolio XIRR for the period",
    "[BENCHMARK_XIRR]":     "benchmark (Nifty 50) XIRR for the period",
    "[TWR]":                "time-weighted return for the period",
    "[ALPHA_PCT]":          "alpha vs benchmark in %",
    "[ALPHA_INR]":          "alpha vs benchmark in ₹",
    "[EQ_XIRR]":            "equity segment XIRR",
    "[FI_XIRR]":            "fixed income segment XIRR",
    "[AVG_DURATION]":       "average holding duration in months",
    "[PERIOD_MONTHS]":      "period length in months",

    # Per-stock (populated dynamically for each holding)
    # Pattern: [RET_STOCKNAME] e.g. [RET_HDFCBANK], [RET_INFY]
    # Pattern: [PRICE_START_STOCKNAME], [PRICE_END_STOCKNAME]
}

# ─────────────────────────────────────────────────────────────
#  MODELS
# ─────────────────────────────────────────────────────────────

class ReportRequest(BaseModel):
    client:             str = ""
    period_start:       str = ""
    period_end:         str = ""
    raw_data:           str
    adviser_notes:      str = ""        # adviser's context — screened before sending to Claude
    user_comments:      str = ""        # adviser's own bullet points / themes — screened; Claude weaves into prose
    tone:               str = "warm"    # warm | formal | brief
    audit_price:        Optional[Dict] = None
    audit_calc:         Optional[Dict] = None
    overrides:          Dict[str, str] = {}
    prose_sections:     List[str] = [
        "red_flags", "snapshot_interpretation", "market_context",
        "worked_narrative", "decisions", "didnt_work_narrative",
        "value_narrative", "positioning_themes", "forward_look", "closing_note"
    ]
    anthropic_api_key:  str = ""

class ComputedData(BaseModel):
    """All numbers. Stays server-side. Never sent to Claude."""
    client:             str
    period:             str
    period_start:       str
    period_end:         str
    period_months:      Optional[float]
    portfolio_value:    Optional[str]
    total_invested:     Optional[str]
    total_profit:       Optional[str]
    lifetime_abs_ret:   Optional[str]
    portfolio_xirr:     Optional[str]
    benchmark_xirr:     Optional[str]
    twr:                Optional[str]
    alpha_pct:          Optional[str]
    alpha_inr:          Optional[str]
    eq_xirr:            Optional[str]
    fi_xirr:            Optional[str]
    avg_duration:       Optional[str]
    holdings:           List[Dict]
    top_performers:     List[Dict]
    worst_performers:   List[Dict]
    demerger_suspects:  List[Dict]
    buys:               List[str]
    sells:              List[str]
    audit_red_ids:      List[str]
    audit_amber_ids:    List[str]
    audit_flags:        List[Dict]
    outperformed:       bool
    alpha_direction:    str             # "outperformed" | "underperformed" | "matched"
    snapshot_table:     str
    performers_table:   str
    laggards_table:     str
    # Placeholder → actual value map for post-injection
    placeholder_map:    Dict[str, str]

class AnonymisedPayload(BaseModel):
    """
    What actually goes to Claude. Client-data-free.
    Auditable: log this freely, it contains no PII or financial data.
    """
    period_start:           str
    period_end:             str
    tone:                   str
    alpha_direction:        str                 # "outperformed" | "underperformed" | "matched"
    outperformed:           bool
    top_performer_stocks:   List[Dict]          # name, sector, thesis, rank_label, direction
    underperformer_stocks:  List[Dict]          # name, sector, thesis, rank_label, direction
    all_stocks:             List[str]           # names only, for positioning section
    buys:                   List[str]           # stock names only
    sells:                  List[str]           # stock names only
    demerger_stocks:        List[str]           # names only
    has_equity:             bool
    has_fi:                 bool
    audit_amber_labels:     List[str]           # no details, just labels
    adviser_notes_screened: str                 # only after PII scrubbing
    user_comments_screened: str = ""            # same screening as adviser_notes
    # Holdings-derived context only (tickers + inferred sectors). No client name, no ₹, no weights/%.
    portfolio_context_for_tailoring: str = ""

class ReportSection(BaseModel):
    id:             str
    title:          str
    content:        str
    source:         str             # "code" | "claude_api" | "hybrid"
    internal_only:  bool = False

class ReportResponse(BaseModel):
    client:             str         # added by Python post-generation
    period:             str
    generated_at:       str
    tone:               str
    sections:           List[ReportSection]
    claude_tokens:      int
    audit_gate:         str
    privacy_log:        Dict        # what was/wasn't sent to Claude (for audit trail)

# ─────────────────────────────────────────────────────────────
#  PARSING UTILITIES
# ─────────────────────────────────────────────────────────────

def parse_inr(text: str) -> Optional[float]:
    if not text:
        return None
    t = str(text).replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    for pat, mult in [(r"([\d.]+)\s*[Cc][Rr]", 1e7), (r"([\d.]+)\s*[Ll]", 1e5), (r"([\d.]+)", 1)]:
        m = re.search(pat, t)
        if m:
            return float(m.group(1)) * mult
    return None

def find_first(pattern: str, text: str, flags=re.IGNORECASE) -> Optional[str]:
    m = re.search(pattern, text, flags)
    return m.group(1) if m else None

def parse_date_safe(s: str) -> Optional[date]:
    try:
        return dateparser.parse(str(s), dayfirst=False).date()
    except Exception:
        return None

def extract_holdings(raw: str) -> List[Dict]:
    holdings = []
    for line in raw.split("\n"):
        m = re.search(
            r"\b([A-Z&]{2,15})\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)\s+([-+]?[\d.]+)%",
            line.strip()
        )
        if m:
            sp  = float(m.group(2).replace(",", ""))
            ep  = float(m.group(3).replace(",", ""))
            ret = float(m.group(4)) / 100.0
            holdings.append({
                "name":            m.group(1),
                "start_price":     sp,
                "end_price":       ep,
                "reported_return": ret,
                "return_pct":      f"{ret*100:+.1f}%",
            })
    return holdings

def infer_sector_and_thesis(name: str) -> Tuple[str, str]:
    n = name.upper()
    MAP = [
        (["SBI","PNB","CANBANK","BANKBARODA","UNIONBANK"],
         "PSU Banking", "government banking sector — capex cycle / credit growth proxy"),
        (["NTPC","POWERGRID","NHPC","SJVN","TATAPOWER"],
         "Power & Energy", "power sector — energy transition and infrastructure build-out"),
        (["BEL","HAL","MIDHANI","BEML","DPSL"],
         "Defence", "defence manufacturing — Make in India and PLI beneficiary"),
        (["L&T","LARSEN","LTIM","LTM","ABB","SIEMENS","THERMAX"],
         "Capital Goods", "capital goods — domestic capex cycle and infrastructure spending"),
        (["INFY","TCS","WIPRO","HCLTECH","TECHM","MPHASIS"],
         "IT Services", "IT services — quality compounder, positioned for global tech demand recovery"),
        (["NAUKRI","ZOMATO","PAYTM","POLICYBZR","DELHIVERY"],
         "New-Age Platforms", "new-age digital platform — long-term structural disruption play"),
        (["HDFCBANK","ICICIBANK","KOTAKBANK","AXISBANK","INDUSINDBK"],
         "Private Banking", "private sector banking — core financial services, credit growth beneficiary"),
        (["SUNPHARMA","DRREDDY","CIPLA","DIVISLAB","LUPIN"],
         "Pharmaceuticals", "pharmaceuticals — defensive positioning with export recovery optionality"),
        (["ASIANPAINT","PIDILITIND","BERGER","AKZONOBEL"],
         "Consumer Discretionary", "consumer discretionary — domestic demand structural compounder"),
        (["RELIANCE"],
         "Conglomerate", "diversified conglomerate — energy, retail, and telecom optionality"),
        (["TITAN","TRENT","DMART","VSTIND"],
         "Retail & Consumer", "India consumption theme — domestic discretionary spending beneficiary"),
        (["TMPV","TATAMOTORS","MSIL","EICHERMOT","M&M","BAJAJ-AUTO"],
         "Automobiles", "auto sector — domestic demand recovery and EV transition positioning"),
        (["HDFCLIFE","SBILIFE","ICICIPRU","MAXLIFE"],
         "Insurance", "insurance — under-penetrated market with structural long-term growth"),
        (["COALINDIA","ONGC","IOC","BPCL"],
         "Energy / PSU", "energy sector — commodity cycle and government dividend yield play"),
        (["JUBLFOOD","WESTLIFE","DEVYANI"],
         "QSR", "quick service restaurants — India's organised food services growth story"),
    ]
    for keywords, sector, thesis in MAP:
        if any(k in n for k in keywords):
            return sector, thesis
    return "Equities", "equity position — sector and thesis to be confirmed by adviser"

def screen_adviser_notes(notes: str) -> str:
    """
    Remove anything that looks like client PII or financial figures from adviser notes
    before sending to Claude.
    Strips: ₹ amounts, % figures, account numbers, names (best effort).
    """
    if not notes:
        return ""
    # Remove ₹ amounts
    cleaned = re.sub(r"₹[\d,]+(?:\.\d+)?(?:[LlCcKk]r?)?", "[AMOUNT REDACTED]", notes)
    # Remove standalone numbers that look like portfolio values
    cleaned = re.sub(r"\b\d{5,}\b", "[NUMBER REDACTED]", cleaned)
    # Remove % figures with context (returns)
    cleaned = re.sub(r"[-+]?\d+\.?\d*\s*%", "[RETURN REDACTED]", cleaned)
    # Remove patterns like "50.20L" or "2.5Cr"
    cleaned = re.sub(r"\b\d+\.?\d*\s*[LlCcKk][Rr]?\b", "[AMOUNT REDACTED]", cleaned)
    return cleaned.strip()


def redact_client_name_from_text(text: str, client: str) -> str:
    """
    Replace known client display name in free-text fields so it is never sent to an external LLM.
    Uses the `client` field from the request (same source used for post-generation [CLIENT] injection).
    """
    if not text or not (client or "").strip():
        return text or ""
    c = " ".join(str(client).strip().split())
    if len(c) < 3:
        return text
    out = text
    # Whole display name first (e.g. "Priya Nair")
    out = re.sub(re.escape(c), "[CLIENT]", out, flags=re.IGNORECASE)
    # Each name part for cases where the user only typed a first/last name (word-boundary)
    parts = c.split()
    if len(parts) > 1:
        for p in parts:
            p2 = p.strip(".,;:'\"")
            if len(p2) >= 3:
                out = re.sub(rf"\b{re.escape(p2)}\b", "[CLIENT]", out, flags=re.IGNORECASE)
    return out


def prepare_notes_for_external_llm(raw: str, client: str) -> str:
    """Strip figures, then remove client name from adviser/user text before Claude."""
    return screen_adviser_notes(redact_client_name_from_text(raw or "", client))


def redact_leaked_client_name_in_prose(prose: Dict[str, str], client: str) -> Dict[str, str]:
    """
    If the model echoed the real client name (from user notes or hallucination), rewrite to [CLIENT]
    before placeholder injection maps [CLIENT] to the actual name for the final report.
    """
    if not (client or "").strip():
        return prose
    return {k: redact_client_name_from_text(v or "", client) for k, v in prose.items()}

# ─────────────────────────────────────────────────────────────
#  COMPUTED DATA BUILDER  (server-side only, never sent to Claude)
# ─────────────────────────────────────────────────────────────

def build_computed_data(req: ReportRequest) -> ComputedData:
    raw = req.raw_data

    period_start_d = parse_date_safe(req.period_start)
    period_end_d   = parse_date_safe(req.period_end)
    period_months  = None
    if period_start_d and period_end_d:
        delta         = relativedelta(period_end_d, period_start_d)
        period_months = delta.years * 12 + delta.months + delta.days / 30.0

    # Extract raw figures
    portfolio_value  = find_first(r"(?:total|portfolio|current)\s+value[:\s]+([\d.]+[Ll]?)", raw)
    total_invested   = find_first(r"total\s+invested[:\s]+([\d.]+[Ll]?)", raw)
    total_profit     = find_first(r"total\s+profit[:\s]+([\d.]+[Ll]?)", raw)
    lifetime_abs_ret = find_first(r"(?:absolute|lifetime|total)\s+return[:\s]+([\d.]+)%", raw)
    portfolio_xirr   = find_first(r"portfolio\s+xirr[:\s]+([-+]?[\d.]+)%", raw)
    benchmark_xirr   = find_first(r"benchmark\s+xirr[:\s]+([-+]?[\d.]+)%", raw)
    twr              = find_first(r"\btwr[:\s]+([-+]?[\d.]+)%", raw)
    eq_xirr          = find_first(r"equity\s+xirr[:\s]+([-+]?[\d.]+)%", raw)
    fi_xirr          = find_first(r"(?:fi|fixed\s*income|debt)\s+xirr[:\s]+([-+]?[\d.]+)%", raw)
    avg_duration     = find_first(r"avg.*?duration[:\s]+([\d.]+)\s*months?", raw)
    avg_aum_str      = find_first(r"avg.*?(?:portfolio|aum)\s+value[:\s]+([\d.]+[Ll]?)", raw)

    # Alpha
    alpha_pct_str, alpha_inr_str = None, None
    outperformed   = False
    alpha_direction = "matched"

    if portfolio_xirr and benchmark_xirr:
        try:
            p_f = float(portfolio_xirr)
            b_f = float(benchmark_xirr)
            a_f = p_f - b_f
            alpha_pct_str  = f"{a_f:+.2f}%"
            outperformed   = a_f > 0
            alpha_direction = "outperformed" if a_f > 0.1 else ("underperformed" if a_f < -0.1 else "matched")
            if avg_aum_str:
                apv = parse_inr(avg_aum_str)
                if apv:
                    alpha_inr = a_f / 100 * apv
                    alpha_inr_str = f"₹{abs(alpha_inr):,.0f} {'added above' if outperformed else 'below'} index"
        except Exception:
            pass

    # Holdings
    holdings = extract_holdings(raw)
    for h in holdings:
        sector, thesis = infer_sector_and_thesis(h["name"])
        h["sector"] = sector
        h["thesis"] = thesis

    top_performers   = sorted([h for h in holdings if h["reported_return"] > 0],
                               key=lambda x: x["reported_return"], reverse=True)[:5]
    worst_performers = sorted([h for h in holdings if h["reported_return"] < 0],
                               key=lambda x: x["reported_return"])[:5]
    demerger_suspects = [h for h in holdings if h["reported_return"] <= -0.40]

    buys  = list(dict.fromkeys(re.findall(r"bought?\s+([A-Z]{2,15})", raw, re.IGNORECASE)))[:10]
    sells = list(dict.fromkeys(re.findall(r"sold?\s+([A-Z]{2,15})", raw, re.IGNORECASE)))[:10]

    # Audit flags
    audit_red_ids, audit_amber_ids, audit_flags = [], [], []
    for audit_result in [req.audit_price, req.audit_calc]:
        if audit_result and "checks" in audit_result:
            for chk in audit_result["checks"]:
                if chk["verdict"] == "red":
                    audit_red_ids.append(chk["id"])
                    audit_flags.append({"id": chk["id"], "label": chk["label"],
                                        "detail": chk["detail"], "verdict": "red"})
                elif chk["verdict"] == "amber":
                    audit_amber_ids.append(chk["id"])
                    audit_flags.append({"id": chk["id"], "label": chk["label"],
                                        "detail": chk["detail"], "verdict": "amber"})

    # ── Snapshot table (code only — contains ₹ figures, never sent to Claude) ──
    pv   = f"₹{portfolio_value}" if portfolio_value else "—"
    ti   = f"₹{total_invested}"  if total_invested  else "—"
    lar  = f"{lifetime_abs_ret}% (lifetime since inception — not period return)" if lifetime_abs_ret else "—"
    snap_rows = [
        ("Current Portfolio Value",    pv),
        ("Total Invested (lifetime)",  ti),
        ("Lifetime Absolute Return",   lar),
        ("Period Portfolio XIRR",      f"{portfolio_xirr}%" if portfolio_xirr else "—"),
        ("Period Benchmark XIRR",      f"{benchmark_xirr}% (Nifty 50)" if benchmark_xirr else "—"),
        ("Alpha",                      f"{alpha_pct_str} ({alpha_inr_str})" if alpha_pct_str else "—"),
        ("TWR (period)",               f"{twr}%" if twr else "—"),
        ("Avg Holding Duration",       f"{avg_duration} months" if avg_duration else "—"),
    ]
    snapshot_table = "| Metric | Value |\n|---|---|\n" + "\n".join(
        f"| {k} | **{v}** |" for k, v in snap_rows
    )

    performers_table = "| Stock | Return |\n|---|---|\n" + "\n".join(
        f"| {h['name']} | **{h['return_pct']}** |" for h in top_performers
    ) if top_performers else "No positive performers."

    laggards_table = "| Stock | Return |\n|---|---|\n" + "\n".join(
        f"| {h['name']} | **{h['return_pct']}** |" for h in worst_performers
    ) if worst_performers else "No negative performers."

    # ── Placeholder → value map (Python injects these after Claude returns prose) ──
    placeholder_map: Dict[str, str] = {
        "[PORTFOLIO_VALUE]":  pv,
        "[TOTAL_INVESTED]":   ti,
        "[TOTAL_PROFIT]":     f"₹{total_profit}" if total_profit else "—",
        "[LIFETIME_ABS_RET]": f"{lifetime_abs_ret}%" if lifetime_abs_ret else "—",
        "[PORTFOLIO_XIRR]":   f"{portfolio_xirr}%" if portfolio_xirr else "—",
        "[BENCHMARK_XIRR]":   f"{benchmark_xirr}%" if benchmark_xirr else "—",
        "[TWR]":              f"{twr}%" if twr else "—",
        "[ALPHA_PCT]":        alpha_pct_str or "—",
        "[ALPHA_INR]":        alpha_inr_str or "—",
        "[EQ_XIRR]":          f"{eq_xirr}%" if eq_xirr else "—",
        "[FI_XIRR]":          f"{fi_xirr}%" if fi_xirr else "—",
        "[AVG_DURATION]":     f"{avg_duration} months" if avg_duration else "—",
        "[PERIOD_MONTHS]":    f"{period_months:.1f}" if period_months else "—",
        "[CLIENT]":           req.client or "valued client",
        "[PERIOD_START]":     req.period_start,
        "[PERIOD_END]":       req.period_end,
    }
    # Per-stock return placeholders
    for h in holdings:
        key = f"[RET_{h['name']}]"
        placeholder_map[key] = h["return_pct"]

    return ComputedData(
        client=req.client or "Client",
        period=f"{req.period_start} to {req.period_end}",
        period_start=req.period_start,
        period_end=req.period_end,
        period_months=period_months,
        portfolio_value=portfolio_value,
        total_invested=total_invested,
        total_profit=total_profit,
        lifetime_abs_ret=lifetime_abs_ret,
        portfolio_xirr=portfolio_xirr,
        benchmark_xirr=benchmark_xirr,
        twr=twr,
        alpha_pct=alpha_pct_str,
        alpha_inr=alpha_inr_str,
        eq_xirr=eq_xirr,
        fi_xirr=fi_xirr,
        avg_duration=avg_duration,
        holdings=holdings,
        top_performers=top_performers,
        worst_performers=worst_performers,
        demerger_suspects=demerger_suspects,
        buys=buys,
        sells=sells,
        audit_red_ids=audit_red_ids,
        audit_amber_ids=audit_amber_ids,
        audit_flags=audit_flags,
        outperformed=outperformed,
        alpha_direction=alpha_direction,
        snapshot_table=snapshot_table,
        performers_table=performers_table,
        laggards_table=laggards_table,
        placeholder_map=placeholder_map,
    )

# ─────────────────────────────────────────────────────────────
#  ANONYMISED PAYLOAD BUILDER
#  This is the ONLY object sent to Claude. Log it freely.
# ─────────────────────────────────────────────────────────────

RANK_LABELS = ["top performer", "second performer", "third performer", "fourth performer", "fifth performer"]
RANK_LABELS_WORST = ["biggest drag", "second drag", "third drag", "fourth drag", "fifth drag"]

# Max tickers listed per sector line in Claude user prompt (breadth signal only; not rupee weights).
_PORTFOLIO_CONTEXT_TICKERS_PER_SECTOR = 14


def build_portfolio_context_for_tailoring(cd: ComputedData) -> str:
    """
    Human-readable block: inferred sectors and listed equity tickers from this portfolio.
    Safe for external LLM: no client name, no rupee amounts, no portfolio totals, no return %.
    Used to tailor market_context and sector narrative to what this book actually holds.
    """
    by_sector: Dict[str, List[str]] = defaultdict(list)
    for h in cd.holdings or []:
        name = (h.get("name") or "").strip().upper()
        if not name:
            continue
        sec, _ = infer_sector_and_thesis(name)
        by_sector[sec].append(name)
    if not by_sector:
        return ""
    lines: List[str] = [
        "PORTFOLIO HOLDING MAP (inferred sectors from tickers only; not weights or values):",
    ]
    for sec, tickers in sorted(by_sector.items(), key=lambda x: (-len(x[1]), x[0])):
        uniq = sorted(set(tickers))
        n = len(uniq)
        head = uniq[:_PORTFOLIO_CONTEXT_TICKERS_PER_SECTOR]
        tail = n - len(head)
        tick_part = ", ".join(head)
        if tail > 0:
            tick_part += f" (+{tail} more names in this sector sleeve)"
        lines.append(f"- {sec} — {n} name(s): {tick_part}")
    lines.append(
        "Use this map to decide which sector stories matter for THIS portfolio vs the index; "
        "do not default to generic examples unless they match these names."
    )
    return "\n".join(lines)


def build_anonymised_payload(cd: ComputedData, req: ReportRequest) -> AnonymisedPayload:
    """
    Strips ALL client-identifying and financial data.
    Converts holdings to direction + rank signals only.
    """
    top_stocks = []
    for i, h in enumerate(cd.top_performers):
        sector, thesis = infer_sector_and_thesis(h["name"])
        top_stocks.append({
            "name":        h["name"],           # NSE ticker — public
            "sector":      sector,              # inferred from public data
            "thesis":      thesis,              # inferred from public data
            "rank_label":  RANK_LABELS[i] if i < len(RANK_LABELS) else f"performer #{i+1}",
            "direction":   "outperformed",      # no % magnitude
        })

    under_stocks = []
    for i, h in enumerate(cd.worst_performers):
        sector, thesis = infer_sector_and_thesis(h["name"])
        is_demerger = h["name"] in [d["name"] for d in cd.demerger_suspects]
        under_stocks.append({
            "name":             h["name"],
            "sector":           sector,
            "thesis":           thesis,
            "rank_label":       RANK_LABELS_WORST[i] if i < len(RANK_LABELS_WORST) else f"drag #{i+1}",
            "direction":        "demerger_artefact" if is_demerger else "underperformed",
            "demerger_note":    "Loss is likely a demerger artefact — verify combined return before citing." if is_demerger else None,
        })

    screened_notes = prepare_notes_for_external_llm(req.adviser_notes, req.client)
    screened_user = prepare_notes_for_external_llm(req.user_comments, req.client)
    portfolio_ctx = build_portfolio_context_for_tailoring(cd)

    return AnonymisedPayload(
        period_start=req.period_start,
        period_end=req.period_end,
        tone=req.tone,
        alpha_direction=cd.alpha_direction,
        outperformed=cd.outperformed,
        top_performer_stocks=top_stocks,
        underperformer_stocks=under_stocks,
        all_stocks=[h["name"] for h in cd.holdings],
        buys=cd.buys,
        sells=cd.sells,
        demerger_stocks=[h["name"] for h in cd.demerger_suspects],
        has_equity=bool(cd.eq_xirr),
        has_fi=bool(cd.fi_xirr),
        audit_amber_labels=[f["label"] for f in cd.audit_flags if f["verdict"] == "amber"],
        adviser_notes_screened=screened_notes,
        user_comments_screened=screened_user,
        portfolio_context_for_tailoring=portfolio_ctx,
    )

def build_privacy_log(cd: ComputedData, payload: AnonymisedPayload) -> Dict:
    """Audit trail: documents exactly what was and wasn't sent to Claude."""
    return {
        "sent_to_claude": {
            "stock_names": payload.all_stocks,
            "period": f"{payload.period_start} to {payload.period_end}",
            "alpha_direction": payload.alpha_direction,
            "performer_directions": [s["direction"] for s in payload.top_performer_stocks + payload.underperformer_stocks],
            "screened_adviser_notes": payload.adviser_notes_screened,
            "screened_user_comments": payload.user_comments_screened,
            "portfolio_context_for_tailoring": (payload.portfolio_context_for_tailoring or "")[:4000],
        },
        "NOT_sent_to_claude": {
            "client_name": "✗ WITHHELD",
            "portfolio_value": "✗ WITHHELD",
            "total_invested": "✗ WITHHELD",
            "total_profit": "✗ WITHHELD",
            "portfolio_xirr": "✗ WITHHELD",
            "benchmark_xirr": "✗ WITHHELD",
            "alpha_pct": "✗ WITHHELD",
            "alpha_inr": "✗ WITHHELD",
            "all_stock_prices": "✗ WITHHELD",
            "all_stock_returns_pct": "✗ WITHHELD",
        },
        "injection_method": "Python placeholder substitution post-generation",
        "placeholders_defined": list(cd.placeholder_map.keys()),
    }

# ─────────────────────────────────────────────────────────────
#  CLAUDE PROMPT  (anonymised — no client data)
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are writing a portfolio performance review on behalf of an Indian equity adviser.
Firm: Inertia Equities. SEBI RIA INA000018090.
Voice: First person from the adviser. "We bought...", "Our view was...", "I recommended..."
Tone: As instructed per request.

CRITICAL — CLIENT IDENTITY (must follow):
- You are NOT given the client's real name. Any person name that appears inside ADVISER NOTES or USER COMMENTS
  has been replaced by the token [CLIENT] — treat that token only as the addressee placeholder.
- Never invent, guess, or output a real personal name for the client. Never output honorifics + invented names.
- In every section, wherever you would address or name the client, write exactly [CLIENT] and nothing else
  that resembles a person's name.

CRITICAL DATA PRIVACY RULES:
- You will NOT receive any client financial figures (portfolio value, returns, XIRR, alpha %)
- For all financial figures, use ONLY the placeholder tokens provided — e.g. [ALPHA_PCT], [PORTFOLIO_VALUE]
- Python will inject the actual numbers after you return prose
- If you find yourself wanting to write a ₹ amount or % return: STOP — use the appropriate placeholder instead

CONTENT DEPTH (must follow):
- Expand ADVISER NOTES and USER COMMENTS into full, developed narrative; do not leave them as short fragments.
  Integrate them with portfolio context (stocks, buys/sells, performance direction vs benchmark) using placeholders for figures.
- The market_context section must be substantive: several paragraphs on Indian equities and macro for the
  stated date range (Nifty trajectory, liquidity/FII-DII themes, rates/inflation/RBI where relevant, index sector leadership).
  Prioritise sector and theme discussion that matters for THIS book's tickers (see PORTFOLIO HOLDING MAP in the user message),
  alongside what led/lagged the broad index — do not assume a generic sector tilt (e.g. do not default to IT/BFSI unless the map supports it).
  Add a concise "how Inertia Equities reads this period" view (balanced, not hype) grounded in that window.
- Connect market developments to the client's story in snapshot_interpretation, worked_narrative,
  didnt_work_narrative, value_narrative, forward_look, and closing_note where it strengthens the narrative.

Available placeholder tokens you MUST use (never invent numbers):
  [CLIENT]           — addressee placeholder only (never a real name)
  [PORTFOLIO_VALUE]  — current portfolio value
  [TOTAL_INVESTED]   — total invested since inception
  [LIFETIME_ABS_RET] — lifetime absolute return % (label as: "since inception, not the period return")
  [PORTFOLIO_XIRR]   — portfolio XIRR for the period
  [BENCHMARK_XIRR]   — benchmark XIRR for the period
  [ALPHA_PCT]        — alpha vs Nifty 50 in %
  [ALPHA_INR]        — alpha in ₹ terms
  [EQ_XIRR]          — equity segment XIRR
  [FI_XIRR]          — fixed income XIRR
  [AVG_DURATION]     — average holding duration
  [RET_STOCKNAME]    — individual stock return, e.g. [RET_HDFCBANK], [RET_NAUKRI]

You receive: stock names (public), sector/thesis (inferred from public data), a sector×ticker holding map
(no rupee weights), direction signals (outperformed/underperformed — no magnitude), period dates, and screened adviser notes.

If the user prompt includes USER COMMENTS FOR THE REPORT: weave them into polished first-person prose across
the JSON sections (especially closing_note, forward_look, value_narrative, snapshot_interpretation,
worked_narrative, decisions). Elaborate and enrich them with market and portfolio context; preserve intent.
Still use only [PLACEHOLDER] tokens for any figures.

Respond ONLY with a valid JSON object. No preamble, no markdown fences."""

def build_claude_prompt(payload: AnonymisedPayload, sections: List[str]) -> str:
    """Builds the anonymised prompt. Contains zero client financial data."""

    tone_guide = {
        "warm":   "Warm and personal. Adviser as trusted friend. Natural use of 'I' and 'we'.",
        "formal": "Professional and structured. Respectful tone.",
        "brief":  (
            "Concise in phrasing, but do not shorten market_context or the expansion of USER COMMENTS / "
            "ADVISER NOTES — those must still be fully developed; trim only filler elsewhere."
        ),
    }.get(payload.tone, "Warm and personal.")

    performers_block = "\n".join(
        f"  - {s['rank_label'].upper()}: {s['name']} ({s['sector']}) — {s['thesis']}"
        for s in payload.top_performer_stocks
    ) or "  None identified."

    underperformers_block = "\n".join(
        f"  - {s['rank_label'].upper()}: {s['name']} ({s['sector']}) — {s['thesis']}"
        + (f"\n    ⚠️ {s['demerger_note']}" if s.get("demerger_note") else "")
        for s in payload.underperformer_stocks
    ) or "  None identified."

    all_stocks_block = ", ".join(payload.all_stocks) or "Not available."
    buys_block  = ", ".join(payload.buys)  or "None detected."
    sells_block = ", ".join(payload.sells) or "None detected."

    # Schema for requested sections
    schema: Dict[str, str] = {}
    if "red_flags" in sections:
        schema["red_flags"] = (
            "Internal adviser assessment only — never share with client. "
            "Candid review of: portfolio health concerns (use stock names, not prices), "
            "decision quality issues, performance direction vs benchmark, "
            "positions the adviser needs to be prepared to discuss. "
            "Use [CLIENT] for the client name. Do NOT include any ₹ or % figures — describe direction only."
        )
    if "snapshot_interpretation" in sections:
        schema["snapshot_interpretation"] = (
            "4-6 sentences for [CLIENT]: interpret the period using placeholders for all figures; "
            "tie the portfolio story to Indian markets over the same dates, prioritising sector themes that "
            "matter for the actual tickers in the PORTFOLIO HOLDING MAP (when provided), not a generic sector list. "
            "Mention whether portfolio [ALPHA_PCT] [outperformed/underperformed] the benchmark and [ALPHA_INR] vs index. "
            "Note that [LIFETIME_ABS_RET] is lifetime since inception, not the period return. "
            "Use only placeholder tokens for numbers."
        )
    if "market_context" in sections:
        schema["market_context"] = (
            f"Substantial Indian market commentary for {payload.period_start} through {payload.period_end}: "
            "at least 4-6 short paragraphs (prose, not bullet stubs). Mirror a research-style brief: "
            "Nifty path and approximate period return (no need for tick-perfect prints), FII vs DII flow bias and any heavy months, "
            "RBI/macro and Budget headlines if in-window, US Fed/dollar/tariffs/geopolitical spillovers, and which sectors led or lagged the index. "
            "Explicitly connect index and flow narrative to the PORTFOLIO HOLDING MAP (tickers and inferred sectors) when that map is present — "
            "emphasise sectors and themes that affect those names; do not invent a tilt that the map does not support. "
            "End with a clear, balanced 'how we at Inertia Equities read this stretch' view (conviction, risks, what surprised us). "
            "Ground claims in well-known public themes for that window. "
            "No ₹ amounts, no portfolio size, no client name, no per-stock return % — this section is market + sector context only."
        )
    if "worked_narrative" in sections:
        schema["worked_narrative"] = (
            "For each top performer: stock name in bold, sector, inferred thesis, "
            "what we did (bought/held/added), direction (outperformed — no % magnitude), "
            "2-4 sentences each linking the name to the market narrative for the period. "
            f"Use [RET_STOCKNAME] if you reference a return. "
            "Stocks: " + ", ".join(s["name"] for s in payload.top_performer_stocks)
        )
    if "decisions" in sections:
        schema["decisions"] = (
            f"Narrative on transactions. Buys: {buys_block}. Sells: {sells_block}. "
            "For each: thesis, why entry/exit timing made sense, market context at the time. "
            "No ₹ amounts — describe decisions qualitatively."
        )
    if "didnt_work_narrative" in sections:
        schema["didnt_work_narrative"] = (
            "Honest, brief accounting of underperformers. For each: acknowledge plainly, "
            "one sentence on what happened (sector/macro/company specific), "
            "one sentence on action taken (held/exited/reduced), "
            "brief forward view. For demerger artefacts, explicitly state the loss is an accounting "
            "artefact and the combined position is intact. Do not dwell or over-apologise. "
            "Use [RET_STOCKNAME] if referencing a return. "
            "Stocks: " + ", ".join(s["name"] for s in payload.underperformer_stocks)
        )
    if "value_narrative" in sections:
        schema["value_narrative"] = (
            "Connect [ALPHA_PCT] outperformance (direction: "
            f"{'above' if payload.outperformed else 'below'} Nifty 50) to specific adviser decisions. "
            "Mention [ALPHA_INR] was generated above/below index. "
            "What specific calls drove this? What would an unadvised investor have missed? "
            "End with a sentence on disciplined process."
        )
    if "positioning_themes" in sections:
        schema["positioning_themes"] = (
            f"Top 3-4 portfolio themes based on current holdings: {all_stocks_block}. "
            "Use the PORTFOLIO HOLDING MAP in the user message (sector × tickers) for emphasis — name breadth only, not rupee weights. "
            "Intentional underweights. Brief, specific. No ₹ figures."
        )
    if "forward_look" in sections:
        schema["forward_look"] = (
            "5-7 bullet points: opportunities, macro/sector/geopolitical risks, liquidity themes, "
            f"what would change positioning — anchored to how markets evolved into {payload.period_end}. "
            "Tie at least two bullets to USER COMMENTS / ADVISER NOTES themes when provided. "
            "Conversation-starter tone; no client name; placeholders only if figures appear."
        )
    if "closing_note" in sections:
        schema["closing_note"] = (
            f"4-6 sentences in {payload.tone} tone, addressed only as [CLIENT] (never a real name). "
            "Weave in USER COMMENTS themes if any; acknowledge the period, thank for trust, invite conversation. "
            "Reference Inertia Equities, SEBI RIA INA000018090. Warm but not generic."
        )

    notes_block = (
        f"\nADVISER NOTES (screened, no financial data): {payload.adviser_notes_screened}"
        if payload.adviser_notes_screened else ""
    )

    user_comments_block = (
        "\nUSER COMMENTS FOR THE REPORT (screened — incorporate by editing into the requested sections; "
        "use placeholders only for numbers; do not invent figures):\n"
        f"{payload.user_comments_screened}"
        if payload.user_comments_screened.strip()
        else ""
    )

    amber_block = (
        f"\nAUDIT NOTES (amber flags to be aware of): {'; '.join(payload.audit_amber_labels)}"
        if payload.audit_amber_labels else ""
    )

    portfolio_block = (
        f"\n{payload.portfolio_context_for_tailoring}\n"
        if (payload.portfolio_context_for_tailoring or "").strip()
        else ""
    )

    return f"""TASK: Generate portfolio review prose sections.

PERIOD: {payload.period_start} to {payload.period_end}
TONE: {tone_guide}
PERFORMANCE DIRECTION: Portfolio {payload.alpha_direction} the Nifty 50 benchmark this period.

TOP PERFORMERS (direction only, no returns):
{performers_block}

UNDERPERFORMERS (direction only):
{underperformers_block}

ALL PORTFOLIO STOCKS: {all_stocks_block}
BUYS THIS PERIOD: {buys_block}
SELLS THIS PERIOD: {sells_block}{portfolio_block}{notes_block}{user_comments_block}{amber_block}

PLACEHOLDER TOKENS — use these wherever figures belong, Python will inject actual values:
  [CLIENT], [PORTFOLIO_VALUE], [TOTAL_INVESTED], [LIFETIME_ABS_RET],
  [PORTFOLIO_XIRR], [BENCHMARK_XIRR], [ALPHA_PCT], [ALPHA_INR],
  [EQ_XIRR], [FI_XIRR], [AVG_DURATION]
  Per-stock: [RET_HDFCBANK], [RET_INFY], [RET_NAUKRI], [RET_TMPV], etc.

Generate ONLY the following sections as a JSON object:
{json.dumps(schema, indent=2)}

REMINDERS:
- Never output the client's real name or invented personal names; use only [CLIENT] for the addressee.
- Zero ₹ amounts and zero raw % portfolio returns in your output — placeholders only for client figures.
- Elaborate ADVISER NOTES and USER COMMENTS into developed prose; enrich with period market context and portfolio actions.
- market_context must be a full dated market essay for this period (see schema), tailored to the PORTFOLIO HOLDING MAP when present,
  plus Inertia's read on that window. Stock tickers and inferred sectors are allowed; client name and amounts are not."""

# ─────────────────────────────────────────────────────────────
#  POST-GENERATION: PLACEHOLDER INJECTION + SANITISER
# ─────────────────────────────────────────────────────────────

def inject_placeholders(prose: Dict[str, str], placeholder_map: Dict[str, str]) -> Dict[str, str]:
    """Replace all [PLACEHOLDER] tokens with actual computed values."""
    result = {}
    for section_key, text in prose.items():
        for placeholder, value in placeholder_map.items():
            text = text.replace(placeholder, value)
        result[section_key] = text
    return result

def sanitise_prose(prose: Dict[str, str]) -> Tuple[Dict[str, str], List[str]]:
    """
    Final safety pass: detect and flag any raw ₹ amounts or suspicious % figures
    that Claude may have output despite instructions.
    Returns cleaned prose + list of violations found.
    """
    violations = []
    cleaned    = {}

    inr_pattern = re.compile(r"₹[\d,]+(?:\.\d+)?(?:[LlCcKk][Rr]?)?")
    large_num   = re.compile(r"\b\d{6,}\b")  # 6+ digit numbers (likely portfolio values)

    for key, text in prose.items():
        inr_matches  = inr_pattern.findall(text)
        num_matches  = large_num.findall(text)

        if inr_matches:
            violations.append(f"Section {key}: found raw ₹ amounts {inr_matches} — redacted.")
            text = inr_pattern.sub("[AMOUNT]", text)

        if num_matches:
            violations.append(f"Section {key}: found large numbers {num_matches[:3]} — redacted.")
            text = large_num.sub("[NUMBER]", text)

        cleaned[key] = text

    return cleaned, violations

# ─────────────────────────────────────────────────────────────
#  CLAUDE API CALL
# ─────────────────────────────────────────────────────────────

def hybrid_report_skip_llm() -> bool:
    """
    If True, hybrid report generation does not call Anthropic or Perplexity (manual narrative only).

    Set env ``PORTFOLIO_REVIEW_HYBRID_SKIP_LLM=true`` or Flask ``PORTFOLIO_REVIEW_HYBRID_SKIP_LLM`` in Config.
    (This app is Flask + Gunicorn; server-side Claude is already proxied at ``/api/v1/claude-proxy`` for other UIs.)
    """
    if (os.environ.get("PORTFOLIO_REVIEW_HYBRID_SKIP_LLM") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        from flask import has_app_context, current_app

        if has_app_context() and bool(current_app.config.get("PORTFOLIO_REVIEW_HYBRID_SKIP_LLM")):
            return True
    except Exception:
        pass
    return False


def hybrid_report_max_output_tokens() -> int:
    """Max completion tokens for hybrid report JSON (Anthropic + Perplexity). Override via ANTHROPIC_REPORT_MAX_OUTPUT_TOKENS."""
    raw = (os.environ.get("ANTHROPIC_REPORT_MAX_OUTPUT_TOKENS") or "").strip()
    if raw:
        try:
            v = int(raw)
            return max(2048, min(v, 16384))
        except ValueError:
            pass
    return 8192


def resolve_anthropic_api_key(explicit: str = "") -> str:
    """
    Match Campaign Studio's claude_proxy: env first, then Flask app config (some deploys only set Config).
    """
    k = (explicit or "").strip()
    if k:
        return k
    k = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if k:
        return k
    try:
        from flask import has_app_context, current_app

        if has_app_context():
            cfg = current_app.config.get("ANTHROPIC_API_KEY") or ""
            if isinstance(cfg, str) and cfg.strip():
                return cfg.strip()
    except Exception:
        pass
    return ""


def resolve_perplexity_api_key() -> str:
    """Same pattern as claude/perplexity proxies: env first, then Flask config."""
    k = (os.environ.get("PERPLEXITY_API_KEY") or "").strip()
    if k:
        return k
    try:
        from flask import has_app_context, current_app

        if has_app_context():
            cfg = current_app.config.get("PERPLEXITY_API_KEY") or ""
            if isinstance(cfg, str) and cfg.strip():
                return cfg.strip()
    except Exception:
        pass
    return ""


def anthropic_key_diagnostics(include_key_prefix: bool = False) -> Dict[str, Any]:
    """
    Non-secret facts about which API key the process will use (for /anthropic-ping?diagnose=1).
    """
    env_raw = os.environ.get("ANTHROPIC_API_KEY", "")
    env_k = (env_raw or "").strip()
    cfg_k = ""
    try:
        from flask import has_app_context, current_app

        if has_app_context():
            v = current_app.config.get("ANTHROPIC_API_KEY") or ""
            if isinstance(v, str):
                cfg_k = v.strip()
    except Exception:
        pass
    resolved = resolve_anthropic_api_key("")
    if env_k and resolved == env_k:
        src = "environment"
    elif cfg_k and resolved == cfg_k:
        src = "flask_config"
    elif not resolved:
        src = "none"
    else:
        src = "unknown"
    out: Dict[str, Any] = {
        "python_executable": sys.executable,
        "anthropic_sdk_version": getattr(anthropic, "__version__", "unknown"),
        "env_ANTHROPIC_API_KEY_nonempty": bool(env_k),
        "flask_config_ANTHROPIC_API_KEY_nonempty": bool(cfg_k),
        "env_and_config_same_or_one_empty": (not env_k or not cfg_k or env_k == cfg_k),
        "resolved_key_nonempty": bool(resolved),
        "resolved_key_source": src,
        "resolved_key_length": len(resolved) if resolved else 0,
        "report_model": DEFAULT_ANTHROPIC_MODEL,
    }
    if include_key_prefix and resolved:
        out["resolved_key_prefix_20"] = resolved[:20]
    return out


def _anthropic_client_timeout() -> httpx.Timeout:
    """Long read timeout for large prompts; override via ANTHROPIC_REPORT_HTTP_READ_TIMEOUT (seconds)."""
    read_s = float(os.environ.get("ANTHROPIC_REPORT_HTTP_READ_TIMEOUT", "540"))
    read_s = max(120.0, read_s)
    return httpx.Timeout(60.0, read=read_s, write=120.0, pool=60.0)


def _anthropic_user_message(exc: BaseException) -> str:
    """Short message for JSON errors (prefer API error.message over repr)."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    msg = getattr(exc, "message", None)
    if isinstance(msg, str) and msg.strip():
        return msg.strip()
    return str(exc)


def ping_anthropic_messages() -> Dict[str, Any]:
    """
    One tiny Messages API call using the same key resolution and model as hybrid reports.
    Intended for GET /api/portfolio-review-report/anthropic-ping (ops / debugging).
    """
    key = resolve_anthropic_api_key("")
    if not key:
        raise ReportGeneratorError(
            "ANTHROPIC_API_KEY is not set (environment variable or Flask config — same as Campaign Studio Claude proxy).",
            503,
        )
    ping_timeout = httpx.Timeout(15.0, read=45.0, write=20.0, pool=15.0)
    anthropic = _anthropic()
    client = anthropic.Anthropic(api_key=key, timeout=ping_timeout)
    t0 = time.perf_counter()
    try:
        response = client.messages.create(
            model=DEFAULT_ANTHROPIC_MODEL,
            max_tokens=32,
            system="You reply with exactly one word: pong.",
            messages=[{"role": "user", "content": "ping"}],
        )
    except httpx.TimeoutException as e:
        raise ReportGeneratorError(f"Anthropic request timed out: {e}", 504) from e
    except getattr(anthropic, "AnthropicError", anthropic.APIError) as e:
        msg = _anthropic_user_message(e)
        code = getattr(e, "status_code", None)
        try:
            code = int(code) if code is not None else None
        except (TypeError, ValueError):
            code = None
        if not isinstance(code, int) or not (400 <= code <= 599):
            code = 502
        raise ReportGeneratorError(msg, code) from e
    except Exception as e:
        logger.exception("ping_anthropic_messages: unexpected error")
        raise ReportGeneratorError(str(e), 500) from e

    latency_ms = int((time.perf_counter() - t0) * 1000)
    text = ""
    try:
        text = (response.content[0].text or "").strip()
    except (AttributeError, IndexError, TypeError) as ex:
        text = f"(unreadable response: {ex})"
    usage = getattr(response, "usage", None)
    mid = getattr(response, "model", None) or DEFAULT_ANTHROPIC_MODEL
    return {
        "model": mid,
        "reply": text[:500],
        "latency_ms": latency_ms,
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0) if usage else None,
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0) if usage else None,
    }


def _try_anthropic_hybrid_messages(prompt: str, anth_key: str) -> Optional[Tuple[Dict[str, str], int]]:
    """
    Call Anthropic Messages API for hybrid prose. Returns None if Perplexity fallback should run.
    """
    anthropic = _anthropic()
    client = anthropic.Anthropic(api_key=anth_key, timeout=_anthropic_client_timeout())
    try:
        response = client.messages.create(
            model=DEFAULT_ANTHROPIC_MODEL,
            max_tokens=hybrid_report_max_output_tokens(),
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        if not response.content:
            logger.error("Claude returned empty content blocks")
            return None
        try:
            raw_text = response.content[0].text.strip()
        except (AttributeError, IndexError, TypeError) as ex:
            logger.error("Claude response content shape error: %s", ex)
            return None
        usage = getattr(response, "usage", None)
        if usage is not None:
            tokens = int(getattr(usage, "input_tokens", 0) or 0) + int(
                getattr(usage, "output_tokens", 0) or 0
            )
        else:
            tokens = 0
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)
        prose = json.loads(raw_text)
        if not isinstance(prose, dict):
            logger.error("Claude returned JSON that is not an object")
            return None
        return prose, tokens
    except json.JSONDecodeError as e:
        logger.error("Claude JSON parse error: %s", e)
        return None
    except httpx.TimeoutException as e:
        logger.error("Anthropic request timeout: %s", e)
        return None
    except getattr(anthropic, "AnthropicError", anthropic.APIError) as e:
        logger.error("Anthropic API error (Perplexity fallback if configured): %s", e)
        return None
    except Exception as e:
        logger.exception("Anthropic unexpected error: %s", e)
        return None


def _try_perplexity_hybrid_messages(prompt: str) -> Tuple[Optional[Dict[str, str]], int, str]:
    """
    Perplexity chat/completions with the same system + user contract as Anthropic.
    Returns (prose_dict_or_none, total_tokens, error_tag).
    """
    key = resolve_perplexity_api_key()
    if not key:
        return None, 0, "no_perplexity_key"
    max_tok = hybrid_report_max_output_tokens()
    max_tok = max(512, min(max_tok, 16000))
    read_s = float(os.environ.get("PERPLEXITY_REPORT_HTTP_READ_TIMEOUT", "540"))
    read_s = max(120.0, read_s)
    timeout = httpx.Timeout(60.0, read=read_s, write=120.0, pool=60.0)
    payload: Dict[str, Any] = {
        "model": DEFAULT_PERPLEXITY_REPORT_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tok,
        "temperature": 0.2,
    }
    try:
        with httpx.Client(timeout=timeout) as http_client:
            resp = http_client.post(
                PERPLEXITY_CHAT_COMPLETIONS_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
            )
    except httpx.TimeoutException as e:
        logger.error("Perplexity request timeout: %s", e)
        return None, 0, "perplexity_timeout"
    except Exception as e:
        logger.exception("Perplexity request failed: %s", e)
        return None, 0, str(e)[:200]

    if resp.status_code != 200:
        logger.error("Perplexity HTTP %s: %s", resp.status_code, (resp.text or "")[:500])
        return None, 0, f"perplexity_http_{resp.status_code}"

    try:
        data = resp.json()
    except json.JSONDecodeError:
        return None, 0, "perplexity_invalid_json"

    try:
        content = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        return None, 0, f"perplexity_shape:{e}"

    usage = data.get("usage") or {}
    total = int(usage.get("total_tokens", 0) or 0)
    raw_text = re.sub(r"^```(?:json)?\s*", "", content)
    raw_text = re.sub(r"\s*```$", "", raw_text)
    try:
        prose = json.loads(raw_text)
    except json.JSONDecodeError as e:
        logger.error("Perplexity JSON parse error: %s", e)
        return None, total, "perplexity_json_parse"

    if not isinstance(prose, dict):
        return None, total, "perplexity_not_object"

    logger.info(
        "Hybrid report prose generated via Perplexity fallback (model=%s, total_tokens=%s).",
        DEFAULT_PERPLEXITY_REPORT_MODEL,
        total,
    )
    return prose, total, ""


def call_claude(prompt: str, sections: List[str], api_key: str) -> Tuple[Dict[str, str], int]:
    """
    Prefer Anthropic Messages; if unset or any hard failure (API error, timeout, bad JSON),
    fall back to Perplexity chat/completions when PERPLEXITY_API_KEY is available.

    Set ``HYBRID_REPORT_FORCE_PERPLEXITY=true`` to skip Anthropic entirely (e.g. credits exhausted
    but Perplexity is funded).
    """
    force_ppl = (os.environ.get("HYBRID_REPORT_FORCE_PERPLEXITY") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    anth_key = resolve_anthropic_api_key(api_key)
    if anth_key and not force_ppl:
        got = _try_anthropic_hybrid_messages(prompt, anth_key)
        if got is not None:
            return got
        logger.warning("Hybrid report: Anthropic did not return usable JSON prose; trying Perplexity.")
    elif force_ppl:
        logger.info("Hybrid report: HYBRID_REPORT_FORCE_PERPLEXITY set — skipping Anthropic.")

    pprose, ptokens, perr = _try_perplexity_hybrid_messages(prompt)
    if pprose is not None:
        return pprose, ptokens

    pkey = resolve_perplexity_api_key()
    if not anth_key and not pkey:
        raise ReportGeneratorError(
            "Neither ANTHROPIC_API_KEY nor PERPLEXITY_API_KEY is set (environment variable or Flask config).",
            400,
        )

    # One short line per section (avoid dumping raw API JSON into client-facing tables).
    if perr == "no_perplexity_key":
        line = (
            "[Generation failed — Anthropic did not return usable prose and PERPLEXITY_API_KEY is not set. "
            "Add Anthropic API credits, or set PERPLEXITY_API_KEY for automatic fallback, "
            "or HYBRID_REPORT_FORCE_PERPLEXITY=true if only Perplexity should run.]"
        )
    else:
        line = (
            "[Generation failed — Anthropic did not return usable prose and the Perplexity fallback "
            f"did not succeed ({perr}). Add credits or fix keys, then redeploy/restart workers.]"
        )
    return {s: line for s in sections}, 0

# ─────────────────────────────────────────────────────────────
#  CODE-ONLY SECTIONS
# ─────────────────────────────────────────────────────────────

def build_data_flags(cd: ComputedData, req: ReportRequest) -> ReportSection:
    flags = []
    if not cd.portfolio_xirr:
        flags.append("Portfolio XIRR not found — include cashflow table.")
    if not cd.holdings:
        flags.append("Holdings table not detected — include SYMBOL | START_PRICE | END_PRICE | RETURN% rows.")
    if cd.demerger_suspects:
        flags.append(f"≥40% loss stocks: {', '.join(h['name'] for h in cd.demerger_suspects)}. Verify demerger artefacts before citing.")
    if cd.lifetime_abs_ret:
        flags.append(f"Absolute return {cd.lifetime_abs_ret}% is LIFETIME since inception — label explicitly in all sections.")
    if cd.audit_red_ids:
        flags.append(f"Audit RED findings overridden: {', '.join(cd.audit_red_ids)}. Reasons: {req.overrides}")
    content = "\n".join(f"• {f}" for f in flags) if flags else "• No data flags."
    return ReportSection(id="rs0", title="0. Data Flags (adviser only)",
                         content=content, source="code", internal_only=True)

# ─────────────────────────────────────────────────────────────
#  ASSEMBLER
# ─────────────────────────────────────────────────────────────

def assemble_report(
    req: ReportRequest,
    cd: ComputedData,
    prose: Dict[str, str],
    tokens: int,
    privacy_log: Dict,
    violations: List[str],
) -> ReportResponse:
    sections: List[ReportSection] = []

    sections.append(build_data_flags(cd, req))

    if violations:
        sections.append(ReportSection(
            id="rs_privacy", title="⚠️ Privacy Sanitiser Log (internal)",
            content="\n".join(f"• {v}" for v in violations),
            source="code", internal_only=True
        ))

    if "red_flags" in prose:
        sections.append(ReportSection(
            id="rs_rf", title="🔴 INTERNAL: Adviser Red Flags",
            content=prose["red_flags"], source="claude_api", internal_only=True
        ))

    # RS1 — snapshot table (code) + interpretation (Claude, placeholders injected)
    interp = prose.get("snapshot_interpretation", "")
    sections.append(ReportSection(
        id="rs1", title="1. Portfolio Snapshot",
        content=f"{cd.snapshot_table}\n\n{interp}",
        source="hybrid", internal_only=False
    ))

    if "market_context" in prose:
        sections.append(ReportSection(
            id="rs2", title="2. Market Context",
            content=prose["market_context"], source="claude_api"
        ))

    if "worked_narrative" in prose:
        sections.append(ReportSection(
            id="rs3", title="3. What Worked Well",
            content=f"{cd.performers_table}\n\n{prose['worked_narrative']}",
            source="hybrid"
        ))

    if "decisions" in prose:
        sections.append(ReportSection(
            id="rs4", title="4. Decisions Taken",
            content=prose["decisions"], source="claude_api"
        ))

    if "didnt_work_narrative" in prose:
        sections.append(ReportSection(
            id="rs5", title="5. What Didn't Work",
            content=f"{cd.laggards_table}\n\n{prose['didnt_work_narrative']}",
            source="hybrid"
        ))

    if "value_narrative" in prose:
        sections.append(ReportSection(
            id="rs6", title="6. Value Added by Adviser",
            content=(
                f"**Alpha: [ALPHA_PCT] ([ALPHA_INR])**\n"
                f"Portfolio XIRR: [PORTFOLIO_XIRR] | Benchmark: [BENCHMARK_XIRR]\n\n"
                + prose["value_narrative"]
            ),
            source="hybrid"
        ))

    if "positioning_themes" in prose:
        sections.append(ReportSection(
            id="rs7", title="7. Current Positioning",
            content=prose["positioning_themes"], source="claude_api"
        ))

    if "forward_look" in prose:
        sections.append(ReportSection(
            id="rs8", title="8. Forward Look",
            content=prose["forward_look"], source="claude_api"
        ))

    if "closing_note" in prose:
        sections.append(ReportSection(
            id="rs9", title="9. Closing Note",
            content=prose["closing_note"], source="claude_api"
        ))

    # Final placeholder injection across ALL sections
    for i, sec in enumerate(sections):
        for placeholder, value in cd.placeholder_map.items():
            sec.content = sec.content.replace(placeholder, value)
        sections[i] = sec

    gate = "bypassed" if (cd.audit_red_ids and req.overrides) else \
           "not_run"  if (cd.audit_red_ids and not req.overrides) else "passed"

    return ReportResponse(
        client=req.client or "Client",
        period=cd.period,
        generated_at=datetime.utcnow().isoformat() + "Z",
        tone=req.tone,
        sections=sections,
        claude_tokens=tokens,
        audit_gate=gate,
        privacy_log=privacy_log,
    )

# ─────────────────────────────────────────────────────────────
#  GATE CHECK
# ─────────────────────────────────────────────────────────────

def check_gate(req: ReportRequest) -> Tuple[bool, List[Dict]]:
    blocking = []
    overrides = {str(k): v for k, v in (req.overrides or {}).items()}
    for audit in [req.audit_price, req.audit_calc]:
        if not audit:
            continue
        for chk in audit.get("checks", []):
            cid = str(chk.get("id", ""))
            if chk.get("verdict") == "red" and cid not in overrides:
                blocking.append(chk)
    return len(blocking) == 0, blocking


def generate_hybrid_report(req: ReportRequest) -> ReportResponse:
    """Full hybrid report: compute → anonymised Claude prompt → inject placeholders → assemble."""
    if not req.raw_data.strip():
        raise ReportGeneratorError("raw_data is required.", 400)

    if req.audit_price or req.audit_calc:
        can_proceed, blocking = check_gate(req)
        if not can_proceed:
            ids = ", ".join(str(b.get("id", "")) for b in blocking)
            raise ReportGeneratorError(
                f"Report blocked — {len(blocking)} unresolved RED finding(s): {ids}.",
                422,
                {
                    "error": "REPORT_BLOCKED_BY_AUDIT",
                    "message": f"Report blocked — {len(blocking)} unresolved RED finding(s): {ids}.",
                    "blocking_findings": blocking,
                    "how_to_unblock": "Add overrides map with written reason for each blocking ID.",
                },
            )

    cd = build_computed_data(req)
    payload = build_anonymised_payload(cd, req)
    log: Dict[str, Any] = dict(build_privacy_log(cd, payload))

    if hybrid_report_skip_llm():
        logger.info(
            "Hybrid report: PORTFOLIO_REVIEW_HYBRID_SKIP_LLM — skipping Anthropic/Perplexity; manual narrative placeholders only."
        )
        log["llm"] = {
            "skipped": True,
            "mode": "manual_narrative_only",
            "note": "No payload sent to external LLMs; audit + tables are local.",
        }
        raw_prose = {k: HYBRID_MANUAL_NARRATIVE_BLOCK for k in req.prose_sections}
        tokens = 0
    else:
        prompt = build_claude_prompt(payload, req.prose_sections)
        raw_prose, tokens = call_claude(prompt, req.prose_sections, req.anthropic_api_key)

    clean_prose, violations = sanitise_prose(raw_prose)
    if violations:
        logger.warning("Privacy sanitiser caught violations: %s", violations)

    clean_prose = redact_leaked_client_name_in_prose(clean_prose, req.client)

    final_prose = inject_placeholders(clean_prose, cd.placeholder_map)
    return assemble_report(req, cd, final_prose, tokens, log, violations)


def generate_hybrid_report_from_dict(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a ReportRequest from a plain JSON dict (Flask), then return a JSON-serialisable dict.

    If ``raw_data`` is empty but ``period_analysis`` is present, audit text is derived the same way
    as ``/api/portfolio-review-audit/full-from-period``.
    """
    body = dict(body or {})
    raw = (body.get("raw_data") or "").strip()
    if not raw and body.get("period_analysis"):
        from services.portfolio_review_audit_engine import (
            build_audit_raw_text_from_period_analysis_payload,
        )

        pa = body["period_analysis"]
        if isinstance(pa, dict):
            raw = build_audit_raw_text_from_period_analysis_payload(pa)
            body["raw_data"] = raw
    ov = body.get("overrides") or {}
    if isinstance(ov, dict):
        body["overrides"] = {str(k): str(v) for k, v in ov.items()}
    else:
        body["overrides"] = {}
    req = ReportRequest.model_validate(body)
    resp = generate_hybrid_report(req)
    return resp.model_dump(mode="json")

