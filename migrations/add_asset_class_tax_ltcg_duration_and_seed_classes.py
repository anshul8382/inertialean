#!/usr/bin/env python3
"""
1) asset_class_tax_ruleset: LTCG duration fields per asset class (months + optional mode override).
2) asset_class: seed common classes for tax mapping (Fixed income, REIT, Gold ETF, etc.) if missing.
"""
import os
import sys

from sqlalchemy import inspect, text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from utils.sql_ddl import alter_table_clause


# Names align with existing portfolio taxonomy (security.asset_class_id) and Section 1 tax seed.
# Do not add parallel labels like "Listed equity" — use "Equity" from the core catalog.
PRESET_ASSET_CLASS_NAMES = [
    "Equity Mutual Funds",
    "Fixed income / Debt",
    "Debt Mutual Funds",
    "Gold ETF",
    "International equity",
    "Hybrid / Balanced",
    "Cash & equivalents",
    "Alternatives",
    "Commodities (non-gold)",
    "Real estate (direct)",
]


def _colnames(table: str) -> set:
    return {c["name"] for c in inspect(db.engine).get_columns(table)}


def upgrade():
    app = create_app()
    with app.app_context():
        if "asset_class_tax_ruleset" in inspect(db.engine).get_table_names():
            cols = _colnames("asset_class_tax_ruleset")
            alters = []
            if "ltcg_minimum_months" not in cols:
                alters.append(
                    "ADD COLUMN ltcg_minimum_months INT NOT NULL DEFAULT 12 "
                    "COMMENT 'Minimum calendar months for LTCG threshold for this class (advisor-set; statute varies)'"
                )
            if "ltcg_eligibility_mode" not in cols:
                alters.append(
                    "ADD COLUMN ltcg_eligibility_mode VARCHAR(32) NULL "
                    "COMMENT 'twelve_months|holding_days; NULL=use global tax rule'"
                )
            if "ltcg_holding_days" not in cols:
                alters.append(
                    "ADD COLUMN ltcg_holding_days INT NULL "
                    "COMMENT 'When mode is holding_days: min calendar days for LTCG (per class)'"
                )
            for clause in alters:
                alter_table_clause(db.session, "asset_class_tax_ruleset", clause)
            if alters:
                db.session.commit()
                print("Altered asset_class_tax_ruleset:", len(alters), "column(s)")
            else:
                print("asset_class_tax_ruleset LTCG columns already present")

        # Seed asset_class rows (requires created_by)
        if "asset_class" not in inspect(db.engine).get_table_names():
            print("asset_class table missing — skip seed")
            return

        from models import AssetClass, User

        u = User.query.filter_by(is_active=True).order_by(User.id.asc()).first()
        if not u:
            print("No active user — skip asset_class seed")
            return

        existing = {
            (r.name or "").strip().lower()
            for r in AssetClass.query.with_entities(AssetClass.name).all()
        }
        added = 0
        for nm in PRESET_ASSET_CLASS_NAMES:
            key = nm.strip().lower()
            if key in existing:
                continue
            db.session.add(
                AssetClass(
                    name=nm[:100],
                    description="Preset for tax optimiser asset-class mapping (LTCG/STCG by class).",
                    created_by=int(u.id),
                )
            )
            existing.add(key)
            added += 1
        if added:
            db.session.commit()
            print(f"Seeded {added} asset_class row(s)")
        else:
            print("asset_class presets already present")


if __name__ == "__main__":
    upgrade()
