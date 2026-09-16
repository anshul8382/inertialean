#!/usr/bin/env python3
"""Add tax_optimiser_live_session for multi-worker interactive review (Gunicorn)."""
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
        if _table_exists("tax_optimiser_live_session"):
            print("tax_optimiser_live_session already exists")
            return
        db.session.execute(
            text(
                """
                CREATE TABLE tax_optimiser_live_session (
                    session_key VARCHAR(80) NOT NULL PRIMARY KEY,
                    user_id INT NOT NULL,
                    payload_json JSON NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX ix_tax_optimiser_live_session_user_id (user_id)
                )
                """
            )
        )
        db.session.commit()
        print("Created tax_optimiser_live_session")


if __name__ == "__main__":
    upgrade()
