#!/usr/bin/env python3
"""Add tax_optimiser_tax_rule: multiple reconfigurable global tax parameter rows; one is_active drives calcs."""
import os
import sys

from sqlalchemy import inspect, text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def _table_exists(name: str) -> bool:
    return name in inspect(db.engine).get_table_names()


def upgrade():
    app = create_app()
    with app.app_context():
        if _table_exists("tax_optimiser_tax_rule"):
            print("tax_optimiser_tax_rule already exists")
            return
        db.session.execute(
            text(
                """
            CREATE TABLE tax_optimiser_tax_rule (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(128) NOT NULL DEFAULT 'Default',
                sort_order INT NOT NULL DEFAULT 0,
                is_active TINYINT(1) NOT NULL DEFAULT 0,
                default_stcg_rate DOUBLE NOT NULL DEFAULT 0.2,
                default_ltcg_rate DOUBLE NOT NULL DEFAULT 0.125,
                default_ltcg_exemption_limit DOUBLE NOT NULL DEFAULT 125000,
                ltcg_holding_days INT NOT NULL DEFAULT 365,
                ltcg_eligibility_mode VARCHAR(32) NOT NULL DEFAULT 'twelve_months',
                trade_charges_pct DOUBLE NOT NULL DEFAULT 0.1,
                section_94_8_months_before_record INT NOT NULL DEFAULT 3,
                section_94_8_months_after_record INT NOT NULL DEFAULT 9,
                stcg_loss_carryforward_years INT NOT NULL DEFAULT 8,
                notes TEXT NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX ix_tax_optimiser_tax_rule_active (is_active)
            )
            """
            )
        )
        db.session.commit()
        print("Created tax_optimiser_tax_rule")

        if _table_exists("tax_optimiser_settings"):
            db.session.execute(
                text(
                    """
                INSERT INTO tax_optimiser_tax_rule (
                    name, sort_order, is_active,
                    default_stcg_rate, default_ltcg_rate, default_ltcg_exemption_limit,
                    ltcg_holding_days, ltcg_eligibility_mode, trade_charges_pct,
                    section_94_8_months_before_record, section_94_8_months_after_record,
                    stcg_loss_carryforward_years, notes
                )
                SELECT
                    'Default (from legacy settings)', 0, 1,
                    default_stcg_rate, default_ltcg_rate, default_ltcg_exemption_limit,
                    ltcg_holding_days,
                    COALESCE(NULLIF(ltcg_eligibility_mode, ''), 'twelve_months'),
                    COALESCE(trade_charges_pct, 0.1),
                    section_94_8_months_before_record, section_94_8_months_after_record,
                    stcg_loss_carryforward_years, notes
                FROM tax_optimiser_settings WHERE id = 1
                LIMIT 1
                """
                )
            )
        db.session.commit()
        cnt_row = db.session.execute(text("SELECT COUNT(*) AS c FROM tax_optimiser_tax_rule")).fetchone()
        cnt = int(cnt_row[0]) if cnt_row else 0
        if cnt == 0:
            db.session.execute(
                text(
                    """
                INSERT INTO tax_optimiser_tax_rule (
                    name, sort_order, is_active,
                    default_stcg_rate, default_ltcg_rate, default_ltcg_exemption_limit,
                    ltcg_holding_days, ltcg_eligibility_mode, trade_charges_pct,
                    section_94_8_months_before_record, section_94_8_months_after_record,
                    stcg_loss_carryforward_years
                ) VALUES (
                    'Default', 0, 1,
                    0.2, 0.125, 125000,
                    365, 'twelve_months', 0.1,
                    3, 9, 8
                )
                """
                )
            )
            db.session.commit()
        print("Seeded tax_optimiser_tax_rule")


if __name__ == "__main__":
    upgrade()
