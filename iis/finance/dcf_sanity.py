"""Sanity checks for DCF and ratio inputs."""
from __future__ import annotations

from iis.models.artifacts import FinancialLineItem
from iis.models.enums import PeriodType

MIN_REVENUE_MN = 1_000.0  # ignore growth-rate / ratio junk rows


def is_estimate_period_label(period_label: str | None) -> bool:
    label = (period_label or "").upper()
    if "PRESS RELEASE" in label:
        return True
    if label.endswith("E") and not label.endswith("AE"):
        return True
    return False


def is_material_revenue(value: float | None) -> bool:
    return value is not None and value >= MIN_REVENUE_MN


def _is_reported_revenue_period(item: FinancialLineItem) -> bool:
    if item.line_item != "total_revenue":
        return False
    if item.statement not in ("income_statement", "key_stats"):
        return False
    if not is_material_revenue(item.value):
        return False
    label = (item.period_label or "").upper()
    if "PRESS RELEASE" in label:
        return False
    if label.endswith("E") and not label.endswith("AE"):
        return False
    if item.period_type == PeriodType.ESTIMATE:
        return False
    return True


def pick_latest_reporting_period(items: list[FinancialLineItem]) -> str | None:
    """Latest fiscal period with material reported revenue (not estimates/press release)."""
    candidates = [i for i in items if _is_reported_revenue_period(i)]
    if not candidates:
        return None
    # Prefer income statement over key stats
    candidates.sort(
        key=lambda x: (
            0 if x.statement == "income_statement" else 1,
            0 if x.period_type == PeriodType.ACTUAL else 1,
            x.period_end or "",
        ),
        reverse=True,
    )
    return candidates[0].period_end or candidates[0].period_label


def get_line_item_for_period(
    items: list[FinancialLineItem],
    line_item: str,
    period_end: str | None,
) -> FinancialLineItem | None:
    if not period_end:
        return None
    matches = [
        i
        for i in items
        if i.line_item == line_item and (i.period_end == period_end or i.period_label == period_end)
    ]
    if not matches:
        return None
    matches.sort(key=lambda x: 0 if x.period_type == PeriodType.ACTUAL else 1)
    return matches[0]


def normalize_margin(value: float | None) -> float | None:
    """Return decimal margin in 0–1 range."""
    if value is None:
        return None
    if value > 1.0:
        value = value / 100.0
    if value <= 0 or value > 0.85:
        return None
    return value


def clamp_dcf_margin(value: float | None, *, default: float = 0.17) -> float:
    v = normalize_margin(value)
    if v is None:
        return default
    return min(max(v, 0.05), 0.55)


def clamp_dcf_fcf_conversion(value: float | None, *, default: float = 0.65) -> float:
    """Historical FCF/EBITDA ratio, clamped to a plausible operating range."""
    if value is None:
        return default
    if value > 1.0:
        value = value / 100.0
    if value <= 0 or value > 1.0:
        return default
    return min(max(value, 0.20), 0.95)


def is_sane_dcf_assumption(key: str, value: float | None) -> bool:
    if value is None:
        return False
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if key == "ebitda_margin":
        return 0.05 <= v <= 0.55
    if key == "fcf_conversion":
        return 0.20 <= v <= 0.95
    if key == "revenue_growth_y1_5":
        return -0.05 <= v <= 0.35
    return True
