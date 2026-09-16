"""
Listed-equity style LTCG eligibility: statute uses holding **more than twelve months**
from acquisition. Implemented as calendar-month arithmetic (not a flat 365-day count).

Fallback mode `holding_days` preserves legacy behaviour for regression / overrides.
"""
from __future__ import annotations

from datetime import date

from dateutil.relativedelta import relativedelta

LTCG_MODE_TWELVE_MONTHS = "twelve_months"
LTCG_MODE_HOLDING_DAYS = "holding_days"


def first_day_after_n_calendar_months(acquisition_date: date, months: int) -> date:
    """Date that completes N full calendar months from acquisition (anniversary date)."""
    n = max(1, int(months))
    return acquisition_date + relativedelta(months=n)


def first_day_after_twelve_months(acquisition_date: date) -> date:
    """Date that completes 12 calendar months from acquisition (anniversary)."""
    return first_day_after_n_calendar_months(acquisition_date, 12)


def is_ltcg_calendar_month_rule(acquisition_date: date, sell_date: date, minimum_months: int) -> bool:
    """
    LTCG if sell is strictly after the N-month anniversary of acquisition.

    Example (N=12): acq 15 Jan 2024 → anniversary 15 Jan 2025 → LTCG from 16 Jan 2025 onward.
    Example (N=24, Gold): acq 15 Jan 2024 → anniversary 15 Jan 2026 → LTCG from 16 Jan 2026 onward.
    """
    return sell_date > first_day_after_n_calendar_months(acquisition_date, minimum_months)


def is_ltcg_twelve_month_rule(acquisition_date: date, sell_date: date) -> bool:
    """Listed-equity style: LTCG if sell is strictly after the 12-month anniversary."""
    return is_ltcg_calendar_month_rule(acquisition_date, sell_date, 12)


def classify_listed_equity_gain_type(
    acquisition_date: date,
    sell_date: date,
    *,
    mode: str,
    min_holding_days: int,
    minimum_ltcg_months: int = 12,
) -> tuple[str, int]:
    """
    Returns (gain_type 'LTCG'|'STCG', calendar_days_held).

    For calendar-month modes (default): LTCG if sell is strictly after the N-month anniversary
    of acquisition. Use minimum_ltcg_months=24 for gold / other assets where statute requires
    >24 months (matches asset_class.tax_ltcg_minimum_months from DB).
    """
    holding_days = (sell_date - acquisition_date).days
    m = (mode or LTCG_MODE_TWELVE_MONTHS).strip().lower()
    if m == LTCG_MODE_HOLDING_DAYS:
        gt = "LTCG" if holding_days >= max(1, int(min_holding_days)) else "STCG"
    else:
        months = max(1, int(minimum_ltcg_months))
        gt = "LTCG" if is_ltcg_calendar_month_rule(acquisition_date, sell_date, months) else "STCG"
    return gt, holding_days


def ltcg_rule_public_label(mode: str) -> str:
    m = (mode or LTCG_MODE_TWELVE_MONTHS).strip().lower()
    if m == LTCG_MODE_HOLDING_DAYS:
        return "LTCG if held ≥ N calendar days (legacy)"
    return "LTCG if sell is after 12 calendar months from acquisition"
