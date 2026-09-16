#!/usr/bin/env python3
"""Per-advisor FY+client interactive review flags for Tax Optimiser report (saved vs email sent)."""
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
        if _table_exists("tax_optimiser_client_review_status"):
            print("tax_optimiser_client_review_status already exists")
            return
        db.session.execute(
            text(
                """
                CREATE TABLE tax_optimiser_client_review_status (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    advisor_user_id INT NOT NULL,
                    client_id INT NOT NULL,
                    fy_start_year INT NOT NULL,
                    review_status VARCHAR(24) NOT NULL DEFAULT 'saved',
                    saved_at DATETIME NULL,
                    email_sent_at DATETIME NULL,
                    last_saved_review_session_id INT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_txo_crs_scope (advisor_user_id, client_id, fy_start_year),
                    INDEX ix_txo_crs_advisor_fy (advisor_user_id, fy_start_year),
                    INDEX ix_txo_crs_client (client_id)
                )
                """
            )
        )
        db.session.commit()
        print("Created tax_optimiser_client_review_status")


if __name__ == "__main__":
    upgrade()
