"""
Standard billing rate presets for agreement configuration.
Amounts are in INR; rates are annual percentages (e.g. 2.0 = 2% p.a.).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

# 0–25L @ 2%, 25–50L @ 1.5%, 50L+ @ 1% (annual on AUA in each band)
EQUITY_STANDARD_TIERS: List[Dict[str, Any]] = [
    {
        "min_amount": Decimal("0"),
        "max_amount": Decimal("2500000"),
        "rate_pct": Decimal("2.0"),
    },
    {
        "min_amount": Decimal("2500000"),
        "max_amount": Decimal("5000000"),
        "rate_pct": Decimal("1.5"),
    },
    {
        "min_amount": Decimal("5000000"),
        "max_amount": None,
        "rate_pct": Decimal("1.0"),
    },
]

PRESET_LABELS = {
    "standard_equity": "Standard equity (0–25L 2%, 25–50L 1.5%, 50L+ 1%)",
}


def preset_slabs(preset_id: str) -> List[Dict[str, Any]]:
    """Return slab dicts with min_amount, max_amount, rate_pct (not yet /100)."""
    if preset_id == "standard_equity":
        return [dict(s) for s in EQUITY_STANDARD_TIERS]
    return []
