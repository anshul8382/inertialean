#!/usr/bin/env python3
"""Add trade_charges_pct and ltcg_eligibility_mode to tax_optimiser_settings (singleton row)."""
import os
import sys

from sqlalchemy import inspect, text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def _column_exists(table: str, column: str) -> bool:
    insp = inspect(db.engine)
    cols = {c["name"] for c in insp.get_columns(table)}
    return column in cols


def upgrade():
    app = create_app()
    with app.app_context():
        if not inspect(db.engine).has_table("tax_optimiser_settings"):
            print("tax_optimiser_settings missing — run add_tax_optimiser_settings_table.py first")
            return
        stmts = []
        if not _column_exists("tax_optimiser_settings", "trade_charges_pct"):
            stmts.append(
                "ALTER TABLE tax_optimiser_settings ADD COLUMN trade_charges_pct DOUBLE NOT NULL DEFAULT 0.1"
            )
        if not _column_exists("tax_optimiser_settings", "ltcg_eligibility_mode"):
            stmts.append(
                "ALTER TABLE tax_optimiser_settings ADD COLUMN ltcg_eligibility_mode "
                "VARCHAR(32) NOT NULL DEFAULT 'twelve_months'"
            )
        for sql in stmts:
            db.session.execute(text(sql))
            print(sql[:80] + "...")
        if stmts:
            db.session.commit()
            print("Done.")
        else:
            print("Columns already exist; nothing to do.")


if __name__ == "__main__":
    upgrade()
