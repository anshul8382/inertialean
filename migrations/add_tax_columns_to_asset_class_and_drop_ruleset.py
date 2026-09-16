#!/usr/bin/env python3
"""
Move per–asset-class tax fields from asset_class_tax_ruleset onto asset_class, then drop legacy table.

Run once per environment after deploying models that define AssetClass.tax_* columns.
Idempotent: skips ALTERs if columns exist; skips DROP if legacy table missing.
"""
import os
import sys

from sqlalchemy import inspect, text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from utils.sql_ddl import alter_table_clause


def _ac_cols() -> set:
    return {c["name"] for c in inspect(db.engine).get_columns("asset_class")}


def upgrade():
    app = create_app()
    with app.app_context():
        if "asset_class" not in inspect(db.engine).get_table_names():
            print("asset_class missing — abort")
            return

        cols = _ac_cols()
        alters = []
        if "tax_ruleset" not in cols:
            alters.append(
                "ADD COLUMN tax_ruleset VARCHAR(32) NOT NULL DEFAULT 'equity' "
                "COMMENT 'Tax optimiser ruleset tag'"
            )
        if "tax_stcg_rate" not in cols:
            alters.append(
                "ADD COLUMN tax_stcg_rate DOUBLE NOT NULL DEFAULT 0.20 COMMENT 'STCG rate 0–1'"
            )
        if "tax_ltcg_rate" not in cols:
            alters.append(
                "ADD COLUMN tax_ltcg_rate DOUBLE NOT NULL DEFAULT 0.125 COMMENT 'LTCG rate 0–1'"
            )
        if "tax_ltcg_exemption_limit" not in cols:
            alters.append(
                "ADD COLUMN tax_ltcg_exemption_limit DOUBLE NOT NULL DEFAULT 125000 "
                "COMMENT 'LTCG exemption cap for this class'"
            )
        if "tax_ltcg_minimum_months" not in cols:
            alters.append(
                "ADD COLUMN tax_ltcg_minimum_months INT NOT NULL DEFAULT 12 "
                "COMMENT 'Min months for LTCG threshold (reference)'"
            )
        if "tax_ltcg_eligibility_mode" not in cols:
            alters.append(
                "ADD COLUMN tax_ltcg_eligibility_mode VARCHAR(32) NULL "
                "COMMENT 'twelve_months|holding_days; NULL=inherit global'"
            )
        if "tax_ltcg_holding_days" not in cols:
            alters.append("ADD COLUMN tax_ltcg_holding_days INT NULL")
        if "tax_rules_active" not in cols:
            alters.append(
                "ADD COLUMN tax_rules_active TINYINT(1) NOT NULL DEFAULT 1 "
                "COMMENT 'Use these tax params when true'"
            )
        if "tax_notes" not in cols:
            alters.append("ADD COLUMN tax_notes TEXT NULL")

        for clause in alters:
            alter_table_clause(db.session, "asset_class", clause)
        if alters:
            db.session.commit()
            print("Altered asset_class:", len(alters), "column(s)")
        else:
            print("asset_class tax columns already present")

        legacy = "asset_class_tax_ruleset" in inspect(db.engine).get_table_names()
        if legacy:
            # Backfill from legacy table where a row exists
            try:
                db.session.execute(
                    text(
                        """
                        UPDATE asset_class ac
                        INNER JOIN asset_class_tax_ruleset r ON r.asset_class_id = ac.id
                        SET
                          ac.tax_ruleset = r.ruleset,
                          ac.tax_stcg_rate = r.stcg_rate,
                          ac.tax_ltcg_rate = r.ltcg_rate,
                          ac.tax_ltcg_exemption_limit = r.ltcg_exemption_limit,
                          ac.tax_ltcg_minimum_months = r.ltcg_minimum_months,
                          ac.tax_ltcg_eligibility_mode = r.ltcg_eligibility_mode,
                          ac.tax_ltcg_holding_days = r.ltcg_holding_days,
                          ac.tax_rules_active = r.is_active,
                          ac.tax_notes = r.notes
                        """
                    )
                )
                db.session.commit()
                print("Backfilled asset_class.tax_* from asset_class_tax_ruleset")
            except Exception as e:
                db.session.rollback()
                print("Backfill skipped or failed:", e)

            db.session.execute(text("DROP TABLE IF EXISTS asset_class_tax_ruleset"))
            db.session.commit()
            print("Dropped asset_class_tax_ruleset")
        else:
            print("No asset_class_tax_ruleset table — skip backfill/drop")


if __name__ == "__main__":
    upgrade()
