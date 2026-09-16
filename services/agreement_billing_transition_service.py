"""
First-year fixed fee → ongoing AUA billing, and valuation-date labelling for agreements/invoices.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from models import Agreement

ADVISORY_FIXED_THEN_AUA = "fixed_then_aua"
FIRST_YEAR_FIXED_ANNUAL_VAR = "first_year_fixed_annual_fee"
FIRST_YEAR_FREQUENCY_VAR = "first_year_billing_frequency"


def _agreement_config(agreement: Agreement) -> Dict[str, Any]:
    if not agreement.agreement_data:
        return {}
    try:
        return json.loads(agreement.agreement_data) or {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _vars_map(agreement: Agreement) -> Dict[str, str]:
    return {
        (v.variable_name or "").strip(): (v.variable_value or "").strip()
        for v in (agreement.variables or [])
    }


def billing_start_date(agreement: Agreement) -> Optional[date]:
    """Agreement billing anchor (variable → schedule → signed date)."""
    vm = _vars_map(agreement)
    raw = vm.get("billing_start_date", "")
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    for s in agreement.billing_schedules or []:
        if getattr(s, "is_active", False) and s.billing_start_date:
            d = s.billing_start_date
            return d.date() if hasattr(d, "date") else d
    raw_sd = getattr(agreement, "signed_date", None)
    if raw_sd is not None:
        return raw_sd.date() if hasattr(raw_sd, "date") else raw_sd
    return None


def years_since_billing_start(agreement: Agreement, billing_date: date) -> int:
    start = billing_start_date(agreement)
    if not start or billing_date <= start:
        return 0
    return (billing_date - start).days // 365


def is_fixed_then_aua_agreement(agreement: Agreement) -> bool:
    vm = _vars_map(agreement)
    model = (vm.get("advisory_model") or _agreement_config(agreement).get("advisory_model") or "").strip()
    return model == ADVISORY_FIXED_THEN_AUA


def resolve_effective_advisory_model(agreement: Agreement, billing_date: date) -> str:
    """
    Return advisory model to use for a given invoice date.

    fixed_then_aua → fixed_fee for first 12 months from billing start, then aua.
    """
    vm = _vars_map(agreement)
    model = (vm.get("advisory_model") or "aua").strip()
    if model != ADVISORY_FIXED_THEN_AUA:
        return model
    if years_since_billing_start(agreement, billing_date) < 1:
        return "fixed_fee"
    return "aua"


def first_year_fixed_annual_fee(agreement: Agreement):
    from decimal import Decimal, InvalidOperation

    vm = _vars_map(agreement)
    cfg = _agreement_config(agreement)
    raw = (
        vm.get(FIRST_YEAR_FIXED_ANNUAL_VAR)
        or cfg.get(FIRST_YEAR_FIXED_ANNUAL_VAR)
        or vm.get("fixed_annual_fee")
        or ""
    ).strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError):
        return None


def first_year_billing_frequency(agreement: Agreement) -> str:
    vm = _vars_map(agreement)
    cfg = _agreement_config(agreement)
    return (
        vm.get(FIRST_YEAR_FREQUENCY_VAR)
        or cfg.get(FIRST_YEAR_FREQUENCY_VAR)
        or vm.get("billing_frequency")
        or cfg.get("billing_frequency")
        or "yearly"
    ).strip() or "yearly"


def format_frequency_label(freq: str) -> str:
    labels = {
        "yearly": "yearly",
        "half_yearly": "half-yearly",
        "quarterly": "quarterly",
    }
    return labels.get((freq or "").strip().lower(), (freq or "yearly").replace("_", "-"))


def _normalize_valuation_rule(rule: Optional[str]) -> str:
    r = (rule or "prepaid").strip().lower()
    if r in ("day_before_period_start", "prepaid"):
        return "prepaid"
    if r == "postpaid":
        return "postpaid"
    return r


def _format_valuation_calendar_day(d: date) -> str:
    """e.g. 31 May (no year — repeats every billing year)."""
    return f"{d.day} {d.strftime('%b')}"


def _period_label(start: date, end: date) -> str:
    return f"{start.strftime('%b')}-{end.strftime('%b')}"


def _period_label_long(start: date, end: date) -> str:
    """e.g. January–June (for agreement wording)."""
    return f"{start.strftime('%B')}–{end.strftime('%B')}"


def _period_end_from_start(start: date, period_months: int) -> date:
    abs_s = start.year * 12 + (start.month - 1)
    abs_next = abs_s + period_months
    ny = abs_next // 12
    nm = abs_next % 12 + 1
    return date(ny, nm, 1) - timedelta(days=1)


def _period_starts_in_cycle(anchor_month: int, period_months: int) -> list:
    """Period start dates for one full billing year (reference year 2025)."""
    year = 2025
    abs_anchor = year * 12 + (anchor_month - 1)
    n_periods = 12 // period_months
    starts = []
    for i in range(n_periods):
        abs_s = abs_anchor + i * period_months
        y = abs_s // 12
        m = abs_s % 12 + 1
        starts.append(date(y, m, 1))
    return starts


def format_aua_billing_basis_summary(
    rule: Optional[str],
    *,
    period_start_month: int = 1,
    frequency: str = "half_yearly",
) -> str:
    """
    Single sentence aligned with the AUA billing basis dropdown (pre-paid / post-paid).
    """
    r = _normalize_valuation_rule(rule)
    freq = (frequency or "yearly").strip().lower()
    anchor = period_start_month if 1 <= period_start_month <= 12 else 1
    span_example = ""
    if freq == "half_yearly" and anchor == 1:
        if r == "prepaid":
            span_example = " (e.g. 31 December for the January–June half-year)"
        else:
            span_example = " (e.g. 30 June for the January–June half-year)"

    if r == "postpaid":
        return (
            "AUA for each billing period is valued at the end of that period "
            f"(post-paid basis{span_example})."
        )
    return (
        "AUA for each billing period is valued at the end of the month immediately "
        f"before the period starts (pre-paid basis{span_example})."
    )


def format_portfolio_valuation_dates_list(
    rule: Optional[str],
    *,
    period_start_month: int = 1,
    frequency: str = "half_yearly",
) -> str:
    """
    Period-first valuation dates for agreements.

    Example (half-yearly, Jan anchor, pre-paid):
      January–June: portfolio valuation as of 31 December;
      July–December: portfolio valuation as of 30 June.

    Post-paid uses period end (e.g. 30 June for January–June).
    """
    r = _normalize_valuation_rule(rule)
    freq = (frequency or "yearly").strip().lower()
    anchor = period_start_month if 1 <= period_start_month <= 12 else 1
    period_months = {"yearly": 12, "half_yearly": 6, "quarterly": 3}.get(freq, 12)

    lines: List[str] = []
    for start in _period_starts_in_cycle(anchor, period_months):
        end = _period_end_from_start(start, period_months)
        span = _period_label_long(start, end)
        val_day = end if r == "postpaid" else start - timedelta(days=1)
        lines.append(
            f"{span}: portfolio valuation as of {_format_valuation_calendar_day(val_day)}"
        )

    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0] + "."
    return "; ".join(lines) + "."


def format_valuation_as_of_period_clause(
    rule: Optional[str],
    *,
    period_start_month: int = 1,
    frequency: str = "half_yearly",
) -> str:
    """
    Short suffix for fee lines: valuation date + billing period span.

    e.g. ``as of 31 May for the Jun-Nov half-year and as of 30 Nov for the Dec-May half-year``
    """
    r = _normalize_valuation_rule(rule)
    freq = (frequency or "yearly").strip().lower()
    anchor = period_start_month if 1 <= period_start_month <= 12 else 1
    period_months = {"yearly": 12, "half_yearly": 6, "quarterly": 3}.get(freq, 12)

    period_word = (
        "half-year"
        if freq == "half_yearly"
        else "quarter"
        if freq == "quarterly"
        else "year"
    )

    chunks: list = []
    for start in _period_starts_in_cycle(anchor, period_months):
        end = _period_end_from_start(start, period_months)
        label = _period_label(start, end)
        if r == "postpaid":
            val_day = end
        else:
            val_day = start - timedelta(days=1)
        chunks.append(
            f"as of {_format_valuation_calendar_day(val_day)} for the {label} {period_word}"
        )

    if not chunks:
        return "as per the agreed valuation dates for each billing period"
    if len(chunks) == 1:
        return chunks[0]
    if len(chunks) == 2:
        return f"{chunks[0]} and {chunks[1]}"
    return ", ".join(chunks[:-1]) + f", and {chunks[-1]}"


def format_portfolio_valuation_schedule(
    rule: Optional[str],
    *,
    period_start_month: int = 1,
    frequency: str = "half_yearly",
) -> str:
    """Agreement valuation dates (period-first list; preferred for <<portfolio_valuation_dates>>)."""
    text = format_portfolio_valuation_dates_list(
        rule,
        period_start_month=period_start_month,
        frequency=frequency,
    )
    if text:
        return text
    r = _normalize_valuation_rule(rule)
    freq = (frequency or "yearly").strip().lower()
    anchor = period_start_month if 1 <= period_start_month <= 12 else 1
    period_months = {"yearly": 12, "half_yearly": 6, "quarterly": 3}.get(freq, 12)

    period_chunks: list = []
    for start in _period_starts_in_cycle(anchor, period_months):
        end = _period_end_from_start(start, period_months)
        label = _period_label(start, end)
        if r == "postpaid":
            val_day = end
        else:
            val_day = start - timedelta(days=1)
        period_chunks.append((_format_valuation_calendar_day(val_day), label))

    if not period_chunks:
        return format_valuation_date_rule_description(
            rule, period_start_month=anchor, frequency=freq
        )

    freq_phrase = format_frequency_label(freq)
    period_word = (
        "half-year"
        if freq == "half_yearly"
        else "quarter"
        if freq == "quarterly"
        else "year"
    )

    if len(period_chunks) == 1:
        day_txt, span = period_chunks[0]
        return (
            "As per the AUA billing rates mentioned above, fees are calculated "
            f"{freq_phrase} on {day_txt} for the {span} {period_word}, each year."
        )

    parts = [
        f"on {day_txt} for the {span} {period_word}"
        for day_txt, span in period_chunks
    ]
    if len(parts) == 2:
        schedule = f"{parts[0]} and {parts[1]}"
    else:
        schedule = ", ".join(parts[:-1]) + f", and {parts[-1]}"

    return (
        "As per the AUA billing rates mentioned above, fees are calculated "
        f"{freq_phrase} {schedule}, each year."
    )


def format_valuation_date_rule_description(
    rule: Optional[str],
    *,
    period_start_month: int = 1,
    frequency: str = "half_yearly",
) -> str:
    """
    Agreement/invoice prose listing valuation date for each billing period.
    """
    return format_portfolio_valuation_schedule(
        rule,
        period_start_month=period_start_month,
        frequency=frequency,
    )


def valuation_date_for_period(
    rule: str,
    period_start: date,
    period_end: date,
) -> Tuple[date, str]:
    """Return (valuation_date, short_label) for a billing period."""
    r = (rule or "prepaid").strip().lower()
    if r == "postpaid":
        return period_end, f"period end ({period_end.isoformat()})"
    val = period_start - timedelta(days=1)
    return val, f"day before period start ({val.isoformat()})"


def format_fixed_then_aua_special_note(
    agreement: Agreement,
    *,
    annual_fee_inr: float,
    first_year_frequency: str,
    ongoing_frequency: Optional[str] = None,
) -> str:
    start = billing_start_date(agreement)
    start_txt = start.strftime("%d %B %Y") if start else "the billing start date"
    vm = _vars_map(agreement)
    cfg = _agreement_config(agreement)
    try:
        anchor = int(vm.get("period_start_month") or cfg.get("period_start_month") or 1)
    except (TypeError, ValueError):
        anchor = 1
    val_rule = vm.get("valuation_date_rule") or cfg.get("valuation_date_rule") or "prepaid"
    ongoing_freq = ongoing_frequency or vm.get("billing_frequency") or "yearly"
    val_desc = format_portfolio_valuation_schedule(
        val_rule,
        period_start_month=anchor,
        frequency=ongoing_freq,
    )
    yr1_freq = format_frequency_label(first_year_frequency)
    from services.agreement_billing_config_service import AUTO_GENERATED_NOTE_PREFIX

    body = (
        f"For the first twelve months from {start_txt}, advisory fees shall be a fixed fee of "
        f"₹{annual_fee_inr:,.0f} per annum, invoiced on a {yr1_freq} basis. "
        f"From the first anniversary of that date onwards, fees shall be calculated on assets under "
        f"advice as per the fee schedule below, with AUA valued {val_desc}."
    )
    return f"{AUTO_GENERATED_NOTE_PREFIX} Fixed-then-AUA] {body}"


def build_transition_billing_notes(
    agreement: Agreement,
    billing_date: date,
    *,
    valuation_date: Optional[date] = None,
    valuation_label: Optional[str] = None,
) -> list:
    notes = []
    if not is_fixed_then_aua_agreement(agreement):
        return notes
    yrs = years_since_billing_start(agreement, billing_date)
    if yrs < 1:
        notes.append(
            "Billing mode: first year fixed fee (fixed-then-AUA agreement). "
            "AUA-based rates apply from the first anniversary of the billing start date."
        )
    else:
        notes.append(
            "Billing mode: AUA-based fees (post first-year fixed-fee period)."
        )
        if valuation_date:
            notes.append(
                f"AUA valuation date: {valuation_label or valuation_date.isoformat()}."
            )
    return notes
