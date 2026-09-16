#!/usr/bin/env python3
"""
Seed Section 1: Asset class rules (India capital gains reference) into ``asset_class`` tax columns
(``tax_*`` fields on the same row as name/description).

`name` values MUST match `asset_class.name` used by securities (e.g. Equity, Fixed Income, Gold).

Run after: migrations/add_tax_columns_to_asset_class_and_drop_ruleset.py
Idempotent: creates missing ``asset_class`` rows; updates tax fields only when still at ORM defaults.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def _is_default_tax(ac) -> bool:
    """True if tax fields look untouched (ORM / DB defaults)."""
    return (
        (getattr(ac, "tax_ruleset", None) or "equity") == "equity"
        and float(getattr(ac, "tax_stcg_rate", 0) or 0) == 0.20
        and float(getattr(ac, "tax_ltcg_rate", 0) or 0) == 0.125
        and float(getattr(ac, "tax_ltcg_exemption_limit", 0) or 0) == 125_000.0
        and int(getattr(ac, "tax_ltcg_minimum_months", 12) or 12) == 12
        and getattr(ac, "tax_ltcg_eligibility_mode", None) is None
        and getattr(ac, "tax_ltcg_holding_days", None) is None
        and bool(getattr(ac, "tax_rules_active", True))
        and not (getattr(ac, "tax_notes", None) or "").strip()
    )


# ruleset: max 32 chars. Rates are illustrative where statute is “slab” (30% placeholder).
# `name` = exact portfolio `asset_class.name` (case-sensitive match to DB convention where mixed).
SECTION_1_ROWS: List[Dict[str, Any]] = [
    {
        "name": "Equity",
        "description": "Listed equity shares (NSE/BSE, STT paid); same bucket as direct equity in this system.",
        "ruleset": "listed_equity",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.20,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 125_000.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": "Holding >12 months for LTCG. STCG 20% (Sec 111A). LTCG 12.5% (Sec 112A). ₹1.25L/yr exemption on LTCG.",
    },
    {
        "name": "Equity ETF",
        "description": "Exchange-traded equity funds; listed, STT on sale.",
        "ruleset": "equity_etf",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.20,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 125_000.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": "Same as listed equity / 111A & 112A where applicable; ₹1.25L/yr LTCG exemption on eligible listed gains.",
    },
    {
        "name": "Equity Mutual Funds",
        "description": ">65% equity allocation.",
        "ruleset": "equity_mf",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.20,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 125_000.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": "Same as listed equity: STCG 20% (111A), LTCG 12.5% (112A), ₹1.25L/yr LTCG exemption.",
    },
    {
        "name": "ELSS Funds",
        "description": "3-yr lock-in; redemptions after lock-in treated as LTCG.",
        "ruleset": "elss",
        "ltcg_minimum_months": 36,
        "stcg_rate": 0.20,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 125_000.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": "3-year lock-in. After lock-in: LTCG 12.5% (112A), ₹1.25L/yr exemption. STCG N/A for typical post-lock-in exit.",
    },
    {
        "name": "Equity F&O",
        "description": "Futures & options on equity.",
        "ruleset": "equity_fno",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.0,
        "ltcg_rate": 0.0,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": "Generally business income (slab), not capital gains. Rates left 0 — model outside CG engine; confirm with CA.",
    },
    {
        "name": "Debt Mutual Funds",
        "description": "≤35% equity; includes relevant FoFs.",
        "ruleset": "debt_mf",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.30,
        "ltcg_rate": 0.30,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": "Finance Act 2023: no separate LTCG benefit — taxed at slab (30% placeholder).",
    },
    {
        "name": "Fixed Income",
        "description": "Listed bonds, NCDs, G-Secs and other fixed-income securities in this bucket.",
        "ruleset": "listed_bonds",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.30,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": ">12 months for LTCG where applicable. STCG at slab. LTCG 12.5% without indexation (post Jul 2024) for listed bonds.",
    },
    {
        "name": "Fixed income / Debt",
        "description": "Unlisted bonds / debentures and broader debt outside listed-bond bucket.",
        "ruleset": "unlisted_bonds",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.30,
        "ltcg_rate": 0.30,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": "Finance Act 2023: many unlisted debt instruments taxed at slab; no separate LTCG regime (placeholder rates equal).",
    },
    {
        "name": "Debt ETF",
        "description": "Exchange-traded debt funds; align with debt MF / slab treatment for planning.",
        "ruleset": "debt_etf",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.30,
        "ltcg_rate": 0.30,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": "Finance Act 2023: typically slab like debt MF (30% placeholder); confirm fund classification.",
    },
    {
        "name": "Real estate (direct)",
        "description": "Residential, commercial, or land (direct holdings).",
        "ruleset": "immovable",
        "ltcg_minimum_months": 24,
        "stcg_rate": 0.30,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": ">24 months for LTCG. STCG slab. LTCG 12.5% OR 20% with indexation — pre-23 Jul 2024 acquisitions: choose lower.",
    },
    {
        "name": "REITs",
        "description": "Real Estate Investment Trusts; STT paid.",
        "ruleset": "reit",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.20,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 125_000.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": ">12 months. STCG 20% (111A), LTCG 12.5% (112A), ₹1.25L/yr LTCG exemption.",
    },
    {
        "name": "Gold",
        "description": "Physical gold / silver (jewellery, bars, coins) mapped to this class in the portfolio.",
        "ruleset": "phys_metal",
        "ltcg_minimum_months": 24,
        "stcg_rate": 0.30,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": ">24 months for LTCG. STCG slab. LTCG 12.5% without indexation post Jul 2024.",
    },
    {
        "name": "Gold ETF",
        "description": "Listed gold ETF (short preset name in DB).",
        "ruleset": "listed_metal_etf",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.30,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": ">12 months for LTCG. Treatment changed from debt-MF-like rules pre–Jul 2024.",
    },
    {
        "name": "Gold ETF / Silver ETF",
        "description": "Listed gold or silver ETFs (combined class in DB).",
        "ruleset": "listed_metal_etf2",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.30,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": "Same statutory treatment as listed precious-metal ETFs; >12 months for LTCG (post Jul 2024 regime).",
    },
    {
        "name": "Sovereign Gold Bonds (SGB)",
        "description": "RBI-issued; 8-year maturity.",
        "ruleset": "sgb",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.20,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": "Sold on exchange: STCG 20% if <12m; LTCG 12.5% if >12m. Redemption at RBI maturity (8 yrs): interest taxable, capital gain on redemption exempt if held to maturity.",
    },
    {
        "name": "International",
        "description": "International / FOF / overseas funds (<65% Indian equity) when mapped to this class.",
        "ruleset": "intl_fof",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.30,
        "ltcg_rate": 0.30,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": "Often slab-like for non–Indian-equity funds (30% placeholder). Also see International equity for predominantly equity overseas exposure.",
    },
    {
        "name": "International equity",
        "description": "Preset class for overseas equity-oriented exposure.",
        "ruleset": "intl_equity",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.30,
        "ltcg_rate": 0.30,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": "Fund-specific: many international equity funds follow equity MF / listed rules; others slab — confirm scheme documents (placeholder slab).",
    },
    {
        "name": "Unlisted Shares",
        "description": "Private companies, startups.",
        "ruleset": "unlisted_shares",
        "ltcg_minimum_months": 24,
        "stcg_rate": 0.30,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": ">24 months for LTCG. LTCG 12.5% without indexation post Jul 2024.",
    },
    {
        "name": "Crypto / VDAs",
        "description": "Virtual Digital Assets.",
        "ruleset": "vda",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.30,
        "ltcg_rate": 0.30,
        "ltcg_exemption_limit": 0.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": "Sec 115BBH: 30% flat on STCG and LTCG. 1% TDS; no expense deduction; restricted loss set-off.",
    },
    {
        "name": "InvITs",
        "description": "Infrastructure Investment Trusts; STT paid.",
        "ruleset": "invit",
        "ltcg_minimum_months": 12,
        "stcg_rate": 0.20,
        "ltcg_rate": 0.125,
        "ltcg_exemption_limit": 125_000.0,
        "ltcg_eligibility_mode": None,
        "ltcg_holding_days": None,
        "notes": ">12 months. STCG 20% (111A), LTCG 12.5% (112A), ₹1.25L/yr LTCG exemption.",
    },
]


def upgrade() -> None:
    app = create_app()
    with app.app_context():
        from sqlalchemy import inspect

        if "asset_class" not in inspect(db.engine).get_table_names():
            print("asset_class table missing — abort")
            return
        ac_cols = {c["name"] for c in inspect(db.engine).get_columns("asset_class")}
        if "tax_ruleset" not in ac_cols:
            print("asset_class.tax_* columns missing — run add_tax_columns_to_asset_class_and_drop_ruleset.py first")
            return

        from models import AssetClass, User
        from services.asset_class_service import apply_tax_fields_from_payload

        u = User.query.filter_by(is_active=True).order_by(User.id.asc()).first()
        if not u:
            print("No active user — abort")
            return

        by_lower: Dict[str, AssetClass] = {
            (r.name or "").strip().lower(): r for r in AssetClass.query.all()
        }

        added_ac = 0
        updated_tax = 0
        skipped_tax = 0

        for row in SECTION_1_ROWS:
            name = (row["name"] or "").strip()[:100]
            key = name.lower()
            ac = by_lower.get(key)
            if ac is None:
                ac = AssetClass(
                    name=name,
                    description=(row.get("description") or "")[:2000] or None,
                    created_by=int(u.id),
                )
                db.session.add(ac)
                db.session.flush()
                by_lower[key] = ac
                added_ac += 1

            if not _is_default_tax(ac):
                skipped_tax += 1
                continue

            apply_tax_fields_from_payload(
                ac,
                {
                    "ruleset": str(row["ruleset"])[:32],
                    "stcg_rate": float(row["stcg_rate"]),
                    "ltcg_rate": float(row["ltcg_rate"]),
                    "ltcg_exemption_limit": float(row["ltcg_exemption_limit"]),
                    "ltcg_minimum_months": int(row["ltcg_minimum_months"]),
                    "ltcg_eligibility_mode": row.get("ltcg_eligibility_mode"),
                    "ltcg_holding_days": row.get("ltcg_holding_days"),
                    "is_active": True,
                    "notes": (row.get("notes") or "")[:8000] or None,
                },
            )
            updated_tax += 1

        db.session.commit()
        print(
            f"Section 1 seed: +{added_ac} asset_class, tax updated {updated_tax}, "
            f"skipped {skipped_tax} (non-default tax preserved)"
        )


if __name__ == "__main__":
    upgrade()
