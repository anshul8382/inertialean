#!/usr/bin/env python3
"""Idempotent: insert tax_optimiser_strategy row book_stcg_gains if missing (S2)."""
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text


def upgrade():
    app = create_app()
    with app.app_context():
        row = db.session.execute(
            text("SELECT id FROM tax_optimiser_strategy WHERE code = :c LIMIT 1"),
            {"c": "book_stcg_gains"},
        ).fetchone()
        if row:
            print("book_stcg_gains strategy row already exists")
            return
        params = {
            "min_net_benefit_inr": 5000,
            "min_tax_saved_to_trade_cost_ratio": 2.0,
            "turnover_sell_buy_multiplier": 2.0,
            "buyback_delay_days": 2,
            "max_stcg_crystallise_inr": 0,
            "min_holding_days_stcg": 0,
            "max_holding_days_stcg": 0,
            "min_days_until_first_ltcg_sell_date": 0,
            "max_days_until_first_ltcg_sell_date": 0,
            "only_nil_cost_bonus_lots": False,
            "exclude_nil_cost_bonus_lots": False,
            "require_fy_stcg_loss_offset": True,
            "max_allowed_incremental_stcg_tax_inr": 0,
        }
        db.session.execute(
            text(
                "INSERT INTO tax_optimiser_strategy (code, enabled, sort_order, params) "
                "VALUES (:code, 1, :so, :params)"
            ),
            {"code": "book_stcg_gains", "so": 15, "params": json.dumps(params)},
        )
        db.session.commit()
        print("Inserted book_stcg_gains strategy row")


if __name__ == "__main__":
    upgrade()
