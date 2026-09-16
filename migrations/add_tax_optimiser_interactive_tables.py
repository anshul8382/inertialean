#!/usr/bin/env python3
"""
Migration: add tables for enhanced tax optimiser (review sessions).

Per–asset-class tax fields live on ``asset_class`` (see
``add_tax_columns_to_asset_class_and_drop_ruleset.py``); this script no longer
creates ``asset_class_tax_ruleset``.
"""
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
        if not _table_exists("tax_optimiser_review_session"):
            db.session.execute(text("""
                CREATE TABLE tax_optimiser_review_session (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    created_by_user_id INT NOT NULL,
                    advisor_user_id INT NOT NULL,
                    client_id INT NULL,
                    fy_label VARCHAR(64) NOT NULL DEFAULT '',
                    status VARCHAR(32) NOT NULL DEFAULT 'in_progress',
                    payload_json JSON NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX ix_tax_optimiser_review_session_created_by_user_id (created_by_user_id),
                    INDEX ix_tax_optimiser_review_session_status (status)
                )
            """))
            db.session.commit()
            print("Created tax_optimiser_review_session")
        else:
            print("tax_optimiser_review_session already exists")


if __name__ == "__main__":
    upgrade()
