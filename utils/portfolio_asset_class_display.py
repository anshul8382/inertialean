"""
Portfolio asset-class ordering and names for UI (holdings, model assignment).

Mutual fund recording uses dedicated asset classes (e.g. Equity Mutual Funds)
separate from direct Equity; keep names aligned with migrations/seed data.
"""
from __future__ import annotations

from typing import Iterable, List, Sequence

EQUITY_MUTUAL_FUNDS = "Equity Mutual Funds"
DEBT_MUTUAL_FUNDS = "Debt Mutual Funds"

MUTUAL_FUND_ASSET_CLASS_NAMES: Sequence[str] = (
    EQUITY_MUTUAL_FUNDS,
    DEBT_MUTUAL_FUNDS,
)

# Per-asset-class security model dropdowns on Assign Models
MODEL_ASSIGNMENT_ASSET_CLASS_NAMES: Sequence[str] = (
    "Equity",
    "Equity ETF",
    EQUITY_MUTUAL_FUNDS,
    "Fixed Income",
    DEBT_MUTUAL_FUNDS,
    "REITs",
    "Gold",
)

# Section order on client holdings (server + client_details.js)
ASSET_CLASS_DISPLAY_ORDER: Sequence[str] = (
    "Equity",
    "Equity ETF",
    EQUITY_MUTUAL_FUNDS,
    "Fixed Income",
    DEBT_MUTUAL_FUNDS,
    "Debt",
    "Gold",
    "REITs",
    "REIT/InvIT",
    "Commodity",
    "International",
    "Hybrid / Balanced",
    "Cash & equivalents",
    "Alternatives",
    "Unknown",
)

_DISPLAY_ALIASES = {
    "DEBT": "Fixed Income",
    "REITS": "REITs",
    "REIT/INVIT": "REIT/InvIT",
    "FIXED INCOME": "Fixed Income",
}


def normalize_asset_class_display_name(name: str | None) -> str:
    raw = (name or "").strip()
    if not raw:
        return "Unknown"
    key = raw.upper()
    return _DISPLAY_ALIASES.get(key, raw)


def asset_class_display_rank(name: str | None) -> int:
    norm = normalize_asset_class_display_name(name)
    for idx, label in enumerate(ASSET_CLASS_DISPLAY_ORDER):
        if norm.lower() == label.lower():
            return idx
    return len(ASSET_CLASS_DISPLAY_ORDER)


def sort_asset_class_keys(keys: Iterable[str]) -> List[str]:
    return sorted(keys, key=lambda k: (asset_class_display_rank(k), (k or "").lower()))


def sort_asset_class_grouped_dict(grouped: dict) -> dict:
    """Return a new dict with keys in display order (Python 3.7+ insertion order)."""
    ordered = sort_asset_class_keys(grouped.keys())
    return {k: grouped[k] for k in ordered}


def is_mutual_fund_security_type(security_type: str | None) -> bool:
    st = (security_type or "").strip().upper().replace(" ", "_")
    return st in ("MUTUAL_FUND", "MUTUALFUND") or "MUTUAL" in st and "FUND" in st
