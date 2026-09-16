#!/usr/bin/env python3
"""Add tax_optimiser_settings singleton row for editable default tax optimisation parameters."""
import os
import sys

from sqlalchemy import inspect, text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def _table_exists(name: str) -> bool:
    inspector = inspect(db.engine)
    return name in inspector.get_table_names()


def upgrade():
    app = create_app()
    with app.app_context():
        if not _table_exists("tax_optimiser_settings"):
            db.session.execute(
                text(
                    """
                CREATE TABLE tax_optimiser_settings (
                    id INT NOT NULL PRIMARY KEY,
                    default_stcg_rate DOUBLE NOT NULL DEFAULT 0.2,
                    default_ltcg_rate DOUBLE NOT NULL DEFAULT 0.125,
                    default_ltcg_exemption_limit DOUBLE NOT NULL DEFAULT 125000,
                    ltcg_holding_days INT NOT NULL DEFAULT 365,
                    section_94_8_months_before_record INT NOT NULL DEFAULT 3,
                    section_94_8_months_after_record INT NOT NULL DEFAULT 9,
                    stcg_loss_carryforward_years INT NOT NULL DEFAULT 8,
                    notes TEXT NULL,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
                """
                )
            )
            db.session.execute(text("INSERT INTO tax_optimiser_settings (id) VALUES (1)"))
            db.session.commit()
            print("Created tax_optimiser_settings")
        else:
            print("tax_optimiser_settings already exists")


if __name__ == "__main__":
    upgrade()
