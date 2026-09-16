#!/usr/bin/env python3
"""tax_optimiser_strategy rows + tax_optimiser_followup_batch for T+N buy-back drafts."""
import os
import sys

from sqlalchemy import inspect, text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def upgrade():
    app = create_app()
    with app.app_context():
        eng = db.engine
        insp = inspect(eng)

        if not insp.has_table("tax_optimiser_strategy"):
            db.session.execute(
                text(
                    """
                CREATE TABLE tax_optimiser_strategy (
                    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    code VARCHAR(64) NOT NULL UNIQUE,
                    enabled TINYINT(1) NOT NULL DEFAULT 1,
                    sort_order INT NOT NULL DEFAULT 0,
                    params JSON NOT NULL,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
                """
                )
            )
            db.session.commit()
            print("Created tax_optimiser_strategy")
        else:
            print("tax_optimiser_strategy already exists")

        if not insp.has_table("tax_optimiser_followup_batch"):
            db.session.execute(
                text(
                    """
                CREATE TABLE tax_optimiser_followup_batch (
                    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    created_by_user_id INT NOT NULL,
                    fy_start_year INT NOT NULL,
                    run_after DATETIME NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'pending',
                    buyback_delay_days INT NOT NULL DEFAULT 2,
                    price_snapshot_json JSON NOT NULL,
                    approved_items_json JSON NOT NULL,
                    draft_html MEDIUMTEXT NULL,
                    draft_html_refreshed MEDIUMTEXT NULL,
                    ops_task_id INT NULL,
                    session_meta_json JSON NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX ix_txo_followup_run_after (run_after),
                    INDEX ix_txo_followup_status (status),
                    INDEX ix_txo_followup_creator (created_by_user_id)
                )
                """
                )
            )
            db.session.commit()
            print("Created tax_optimiser_followup_batch")
        else:
            print("tax_optimiser_followup_batch already exists")

        # Seed default strategies (idempotent)
        cnt = int(db.session.execute(text("SELECT COUNT(*) FROM tax_optimiser_strategy")).scalar() or 0)
        if cnt == 0:
            import json

            seeds = [
                (
                    "book_ltcg_exemption",
                    10,
                    {
                        "min_net_benefit_inr": 5000,
                        "min_tax_saved_to_trade_cost_ratio": 2.0,
                        "turnover_sell_buy_multiplier": 2.0,
                        "buyback_delay_days": 2,
                        "max_securities_per_client": 50,
                    },
                ),
                (
                    "book_stcg_gains",
                    15,
                    {
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
                    },
                ),
                (
                    "defer_to_ltcg",
                    20,
                    {
                        "enabled": True,
                        "max_days_until_ltcg_eligible": 365,
                        "min_unrealized_gain_inr": 0,
                    },
                ),
                (
                    "harvest_unrealized_loss",
                    30,
                    {
                        "enabled": True,
                        "min_net_benefit_inr": 5000,
                        "min_tax_saved_to_trade_cost_ratio": 2.0,
                        "turnover_sell_buy_multiplier": 2.0,
                        "apply_94_8_ranking": True,
                    },
                ),
                (
                    "realised_loss_review",
                    40,
                    {"enabled": True},
                ),
                (
                    "section_94_8_ranking_overlay",
                    50,
                    {"enabled": True},
                ),
            ]
            for code, so, params in seeds:
                db.session.execute(
                    text(
                        "INSERT INTO tax_optimiser_strategy (code, enabled, sort_order, params) "
                        "VALUES (:code, 1, :so, :params)"
                    ),
                    {"code": code, "so": so, "params": json.dumps(params)},
                )
            db.session.commit()
            print("Seeded tax_optimiser_strategy defaults")
        else:
            print("tax_optimiser_strategy already seeded")


if __name__ == "__main__":
    upgrade()
