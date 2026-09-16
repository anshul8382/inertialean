"""
Pure incentive math for dry-run / simulator (external sales slabs, internal SIP, lump sum).
Visibility: access_control (roles + temporary Sharveen hack for sales + internal sections).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from access_control import (
    user_is_ops_manager,
    user_is_sharveen,
    user_sees_incentive_sales_simulator,
)

# External: T ≤ 25k → 0%; 25,001–50,000 → 10% of T; >50k → 12.5% of T
EXTERNAL_TIER_0_MAX = Decimal("25000")
EXTERNAL_TIER_1_MAX = Decimal("50000")
RATE_EXTERNAL_1 = Decimal("0.10")
RATE_EXTERNAL_2 = Decimal("0.125")
INTERNAL_PCT = Decimal("0.005")  # 0.5%
SIP_NOTIONAL_MULTIPLIER = Decimal("10")


def user_sees_sales_incentive_section(user) -> bool:
    return user_sees_incentive_sales_simulator(user)


def user_sees_internal_incentive_section(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return bool(
        getattr(user, "is_admin", False)
        or getattr(user, "is_manager", False)
        or user_is_ops_manager(user)
        or user_is_sharveen(user)
    )


def user_sees_any_incentive_sim_section(user) -> bool:
    return user_sees_sales_incentive_section(user) or user_sees_internal_incentive_section(user)


def _money(x: Any) -> Decimal:
    if x is None:
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    try:
        return Decimal(str(x).strip().replace(",", "") or "0")
    except Exception:
        return Decimal("0")


def external_monthly_incentive(total_qualifying: Decimal) -> Decimal:
    T = max(_money(total_qualifying), Decimal("0"))
    if T <= EXTERNAL_TIER_0_MAX:
        return Decimal("0")
    if T <= EXTERNAL_TIER_1_MAX:
        return (T * RATE_EXTERNAL_1).quantize(Decimal("0.01"))
    return (T * RATE_EXTERNAL_2).quantize(Decimal("0.01"))


def external_slab_detail(total_qualifying: Decimal) -> Dict[str, Any]:
    T = max(_money(total_qualifying), Decimal("0"))
    incentive = external_monthly_incentive(T)

    if T <= EXTERNAL_TIER_0_MAX:
        current_slab = "0% (up to ₹25,000)"
        applied_rate = "0%"
    elif T <= EXTERNAL_TIER_1_MAX:
        current_slab = "10% (₹25,001 – ₹50,000)"
        applied_rate = "10% of full monthly total"
    else:
        current_slab = "12.5% (above ₹50,000)"
        applied_rate = "12.5% of full monthly total"

    next_threshold: Optional[Decimal] = None
    next_slab_label = ""
    if T < EXTERNAL_TIER_0_MAX + Decimal("1"):
        next_threshold = EXTERNAL_TIER_0_MAX + Decimal("1")
        next_slab_label = "10% tier"
    elif T < EXTERNAL_TIER_1_MAX + Decimal("1"):
        next_threshold = EXTERNAL_TIER_1_MAX + Decimal("1")
        next_slab_label = "12.5% tier"

    gap = Decimal("0")
    incentive_at_next = incentive
    if next_threshold is not None:
        gap = (next_threshold - T).quantize(Decimal("0.01"))
        if gap < 0:
            gap = Decimal("0")
        incentive_at_next = external_monthly_incentive(next_threshold)

    return {
        "total_t": str(T),
        "current_slab": current_slab,
        "applied_rate_description": applied_rate,
        "incentive": str(incentive),
        "next_threshold": str(next_threshold) if next_threshold is not None else None,
        "next_slab_label": next_slab_label or None,
        "gap_to_next_slab": str(gap),
        "incentive_at_next_threshold": str(incentive_at_next),
        "is_top_tier": T > EXTERNAL_TIER_1_MAX,
    }


def internal_sip_one_time_incentive(delta_sip_monthly: Decimal) -> Decimal:
    d = max(_money(delta_sip_monthly), Decimal("0"))
    notional = (d * SIP_NOTIONAL_MULTIPLIER).quantize(Decimal("0.01"))
    return (notional * INTERNAL_PCT).quantize(Decimal("0.01"))


def internal_lump_sum_incentive(lump_amount: Decimal) -> Decimal:
    a = max(_money(lump_amount), Decimal("0"))
    return (a * INTERNAL_PCT).quantize(Decimal("0.01"))


def run_simulation(
    sales_monthly_total: Any,
    delta_sip: Any,
    lump_amount: Any,
    show_sales: bool,
    show_internal: bool,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "show_sales": show_sales,
        "show_internal": show_internal,
        "external": None,
        "internal_sip": None,
        "internal_lump": None,
        "combined_incentive": "0",
    }
    combined = Decimal("0")

    if show_sales:
        T = _money(sales_monthly_total)
        ext_inc = external_monthly_incentive(T)
        combined += ext_inc
        out["external"] = {
            "slab": external_slab_detail(T),
            "incentive": str(ext_inc),
        }

    if show_internal:
        ds = _money(delta_sip)
        lump = _money(lump_amount)
        sip_inc = internal_sip_one_time_incentive(ds)
        lump_inc = internal_lump_sum_incentive(lump)
        combined += sip_inc + lump_inc
        notional = (max(ds, Decimal("0")) * SIP_NOTIONAL_MULTIPLIER).quantize(Decimal("0.01"))
        out["internal_sip"] = {
            "delta_sip": str(ds),
            "notional": str(notional),
            "incentive": str(sip_inc),
            "formula": "0.5% × (monthly SIP increase × 10), one-time after eligibility",
        }
        out["internal_lump"] = {
            "amount": str(lump),
            "incentive": str(lump_inc),
            "formula": "0.5% × lump sum amount",
        }

    out["combined_incentive"] = str(combined.quantize(Decimal("0.01")))
    return out
