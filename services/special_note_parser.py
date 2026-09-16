"""
Heuristic parser for agreement special_note free text → structured billing hints.

Used by scripts/scan_agreement_special_notes.py for dry-run review only.
Does not write to the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ParsedSpecialNote:
    """Proposed billing fields extracted from special_note text."""

    billing_frequency: Optional[str] = None
    period_start_month: Optional[int] = None
    valuation_date_rule: Optional[str] = None
    advisory_model: Optional[str] = None
    annual_rate_pct: Optional[float] = None  # e.g. 0.25 means 0.25% annual
    fixed_annual_fee_inr: Optional[float] = None
    flags: List[str] = field(default_factory=list)
    confidence: str = "low"  # low | medium | high

    def to_dict(self) -> Dict[str, Any]:
        return {
            "billing_frequency": self.billing_frequency,
            "period_start_month": self.period_start_month,
            "valuation_date_rule": self.valuation_date_rule,
            "advisory_model": self.advisory_model,
            "annual_rate_pct": self.annual_rate_pct,
            "fixed_annual_fee_inr": self.fixed_annual_fee_inr,
            "flags": list(self.flags),
            "confidence": self.confidence,
        }


_RE_HALF = re.compile(
    r"\b(half[\s-]?yearly|half[\s-]?year|semi[\s-]?annual|6[\s-]?monthly|twice\s+a\s+year)\b",
    re.I,
)
_RE_QUARTER = re.compile(r"\b(quarterly|quarter)\b", re.I)
_RE_YEARLY = re.compile(
    r"\b(yearly|annual|once\s+a\s+year|per\s+annum|p\.?\s*a\.?)\b",
    re.I,
)
_RE_JAN_JUN = re.compile(r"\bjan[\s\-–—]*jun\b", re.I)
_RE_JUL_DEC = re.compile(r"\bjul[\s\-–—]*dec\b", re.I)
_RE_APR_SEP = re.compile(r"\bapr[\s\-–—]*sep\b", re.I)
_RE_OCT_MAR = re.compile(r"\boct[\s\-–—]*mar\b", re.I)
_RE_PREPAID = re.compile(
    r"\b(prepaid|pre[\s-]?paid|advance|day\s+before|end\s+of\s+(the\s+)?previous)\b",
    re.I,
)
_RE_POSTPAID = re.compile(
    r"\b(postpaid|post[\s-]?paid|arrears|end\s+of\s+(the\s+)?(current\s+)?period)\b",
    re.I,
)
_RE_AUA = re.compile(
    r"\b(aua|assets?\s+under\s+advice|advisory\s+fee\s+on\s+aua)\b",
    re.I,
)
_RE_FIXED = re.compile(
    r"\b(fixed\s+fee|flat\s+fee|fixed\s+annual)\b",
    re.I,
)
_RE_PCT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:%|percent|pct)",
    re.I,
)
_RE_BPS = re.compile(r"(\d+(?:\.\d+)?)\s*bps\b", re.I)
_RE_INR = re.compile(
    r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)|([\d,]+(?:\.\d{1,2})?)\s*(?:₹|rs\.?|inr)",
    re.I,
)
_RE_RATE_AT = re.compile(
    r"@\s*(\d+(?:\.\d+)?)\s*%",
    re.I,
)


def _pick_rate_near_half_yearly(note: str, rates: List[float]) -> Optional[float]:
    """When note lists yearly % and half-yearly @ %, prefer rate after 'half yearly'."""
    m = re.search(r"half\s*yearly", note, re.I)
    if not m:
        return None
    tail = note[m.end() : m.end() + 120]
    for rate in rates:
        pat = rf"@?\s*{re.escape(str(rate))}\s*%"
        if re.search(pat, tail, re.I):
            return rate
    return None


def _normalize_note(text: Optional[str]) -> str:
    if not text:
        return ""
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    return t


def parse_special_note(text: Optional[str]) -> ParsedSpecialNote:
    """
    Extract structured billing hints from agreement special_note prose.
    """
    note = _normalize_note(text)
    out = ParsedSpecialNote()
    if not note:
        out.flags.append("empty_note")
        return out

    lower = note.lower()

    # Frequency (mutually exclusive priority: half > quarter > yearly)
    if _RE_HALF.search(note):
        out.billing_frequency = "half_yearly"
    elif _RE_QUARTER.search(note):
        out.billing_frequency = "quarterly"
    elif _RE_YEARLY.search(note):
        out.billing_frequency = "yearly"

    # Period anchor from explicit month ranges
    if _RE_JAN_JUN.search(note) or _RE_JUL_DEC.search(note):
        out.period_start_month = 1
    elif _RE_APR_SEP.search(note) or _RE_OCT_MAR.search(note):
        out.period_start_month = 4

    # Valuation rule
    if _RE_POSTPAID.search(note):
        out.valuation_date_rule = "postpaid"
    elif _RE_PREPAID.search(note):
        out.valuation_date_rule = "prepaid"
    elif "31 dec" in lower or "31-dec" in lower or "30 jun" in lower:
        # Common prepaid examples in form help text
        if "advance" in lower or "previous" in lower:
            out.valuation_date_rule = "prepaid"

    # Advisory model
    if _RE_FIXED.search(note):
        out.advisory_model = "fixed_fee"
    if _RE_AUA.search(note) or "based on aua" in lower:
        if out.advisory_model == "fixed_fee":
            out.flags.append("mixed_aua_and_fixed")
        else:
            out.advisory_model = "aua"

    # Percent rates — collect all; flag multiples
    pcts: List[float] = []
    for m in _RE_RATE_AT.finditer(note):
        try:
            pcts.append(float(m.group(1)))
        except ValueError:
            pass
    for m in _RE_PCT.finditer(note):
        try:
            pcts.append(float(m.group(1)))
        except ValueError:
            pass
    for m in _RE_BPS.finditer(note):
        try:
            pcts.append(float(m.group(1)) / 100.0)
        except ValueError:
            pass

    unique_pcts = sorted(set(round(p, 6) for p in pcts))
    if len(unique_pcts) == 1:
        out.annual_rate_pct = unique_pcts[0]
    elif len(unique_pcts) > 1:
        # Prefer explicit "@ 0.5%" over headline "Yearly ... 1% of AUA"
        at_rates = [float(m.group(1)) for m in _RE_RATE_AT.finditer(note)]
        if at_rates:
            out.annual_rate_pct = at_rates[0]
            if len(set(at_rates)) > 1:
                out.flags.append(f"multiple_at_rates:{at_rates}")
        else:
            chosen = _pick_rate_near_half_yearly(note, unique_pcts)
            if chosen is not None:
                out.annual_rate_pct = chosen
            else:
                out.flags.append(f"multiple_rates:{unique_pcts}")

    # Fixed fee INR
    inr_amounts: List[float] = []
    for m in _RE_INR.finditer(note):
        raw = m.group(1) or m.group(2)
        if raw:
            try:
                inr_amounts.append(float(raw.replace(",", "")))
            except ValueError:
                pass
    if len(inr_amounts) == 1:
        out.fixed_annual_fee_inr = inr_amounts[0]
    elif len(inr_amounts) > 1:
        out.flags.append(f"multiple_inr:{inr_amounts}")

    if out.fixed_annual_fee_inr and not out.advisory_model:
        out.advisory_model = "fixed_fee"

    out.confidence = _score_confidence(out)
    return out


def _score_confidence(parsed: ParsedSpecialNote) -> str:
    if parsed.flags and any(
        f.startswith(("multiple_", "mixed_", "empty_")) for f in parsed.flags
    ):
        return "low"

    signals = 0
    if parsed.billing_frequency:
        signals += 1
    if parsed.period_start_month:
        signals += 1
    if parsed.valuation_date_rule:
        signals += 1
    if parsed.advisory_model:
        signals += 1
    if parsed.annual_rate_pct is not None:
        signals += 1
    if parsed.fixed_annual_fee_inr is not None:
        signals += 1

    if signals >= 3 and "mixed_aua_and_fixed" not in parsed.flags:
        return "high"
    if signals >= 2:
        return "medium"
    return "low"


_RE_YEARLY_AUA_PCT = re.compile(
    r"(?:yearly\s+investment\s+advisory\s+fee|advisory\s+fee|fee)\s*[:.]?\s*"
    r"(\d+(?:\.\d+)?)\s*%\s*(?:of\s+)?aua",
    re.I,
)
_RE_PER_ANNUM_AUA = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*per\s+annum\s+of\s+aua",
    re.I,
)


def resolve_annual_rate_pct_for_storage(
    note: str, parsed: ParsedSpecialNote
) -> Optional[float]:
    """
    Map note text to annual % stored in billing_rate_structure (engine prorates by frequency).

    Prefer explicit yearly-of-AUA headline over half-yearly @ rate when both appear.
    """
    if any(f.startswith("multiple_rates:") for f in parsed.flags):
        for pat in (_RE_YEARLY_AUA_PCT, _RE_PER_ANNUM_AUA):
            m = pat.search(note)
            if m:
                return float(m.group(1))
        return None

    if parsed.annual_rate_pct is None:
        for pat in (_RE_YEARLY_AUA_PCT, _RE_PER_ANNUM_AUA):
            m = pat.search(note)
            if m:
                return float(m.group(1))
        return None

    rate = parsed.annual_rate_pct
    for pat in (_RE_YEARLY_AUA_PCT, _RE_PER_ANNUM_AUA):
        m = pat.search(note)
        if m:
            yearly = float(m.group(1))
            if yearly >= rate:
                return yearly

    if parsed.billing_frequency == "half_yearly" and rate is not None:
        if re.search(
            rf"half\s*yearly\s*@\s*{re.escape(str(rate).rstrip('0').rstrip('.'))}\s*%",
            note,
            re.I,
        ):
            return rate * 2.0

    return rate


def diff_against_current(
    parsed: ParsedSpecialNote,
    current: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compare parsed hints to current agreement billing state.
    current keys: billing_frequency, period_start_month, valuation_date_rule,
                  advisory_model, rate_pct_display (float %), fixed_annual_fee
    """
    conflicts: List[str] = []
    gaps: List[str] = []

    def _norm_freq(v):
        return (v or "").strip().lower() or None

    proposed = parsed.to_dict()

    if parsed.billing_frequency:
        cur = _norm_freq(current.get("billing_frequency"))
        if not cur:
            gaps.append("billing_frequency")
        elif cur != parsed.billing_frequency:
            conflicts.append(f"billing_frequency: current={cur} proposed={parsed.billing_frequency}")

    if parsed.period_start_month is not None:
        try:
            cur_m = int(current.get("period_start_month") or 1)
        except (TypeError, ValueError):
            cur_m = 1
        if cur_m != parsed.period_start_month:
            if current.get("period_start_month") in (None, "", "1") and parsed.period_start_month == 1:
                pass
            else:
                conflicts.append(
                    f"period_start_month: current={cur_m} proposed={parsed.period_start_month}"
                )
        if not current.get("period_start_month"):
            gaps.append("period_start_month")

    if parsed.valuation_date_rule:
        cur = (current.get("valuation_date_rule") or "prepaid").strip().lower()
        if cur != parsed.valuation_date_rule:
            conflicts.append(
                f"valuation_date_rule: current={cur} proposed={parsed.valuation_date_rule}"
            )

    if parsed.advisory_model:
        cur = (current.get("advisory_model") or "").strip().lower()
        if not cur:
            gaps.append("advisory_model")
        elif cur != parsed.advisory_model:
            conflicts.append(f"advisory_model: current={cur} proposed={parsed.advisory_model}")

    if parsed.annual_rate_pct is not None:
        cur_rates = current.get("rate_pcts") or []
        if not cur_rates:
            gaps.append("billing_rates")
        else:
            for r in cur_rates:
                if abs(r - parsed.annual_rate_pct) > 0.001:
                    conflicts.append(
                        f"rate_pct: current={r}% proposed={parsed.annual_rate_pct}%"
                    )
                    break

    if parsed.fixed_annual_fee_inr is not None:
        cur_fee = current.get("fixed_annual_fee")
        if cur_fee is None:
            gaps.append("fixed_annual_fee")
        elif abs(float(cur_fee) - parsed.fixed_annual_fee_inr) > 0.01:
            conflicts.append(
                f"fixed_annual_fee: current={cur_fee} proposed={parsed.fixed_annual_fee_inr}"
            )

    return {
        "proposed": proposed,
        "gaps": gaps,
        "conflicts": conflicts,
        "would_fill": bool(gaps) and not conflicts and parsed.confidence in ("medium", "high"),
    }
