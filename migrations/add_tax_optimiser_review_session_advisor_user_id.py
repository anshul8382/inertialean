#!/usr/bin/env python3
"""
Add advisor_user_id to tax_optimiser_review_session if missing (some DBs added it manually
with NOT NULL and no default; the app now always sets it = created_by_user_id).
"""
import os
import sys

from sqlalchemy import inspect, text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def upgrade():
    app = create_app()
    with app.app_context():
        insp = inspect(db.engine)
        if "tax_optimiser_review_session" not in insp.get_table_names():
            print("tax_optimiser_review_session: table missing, skip")
            return
        cols = {c["name"] for c in insp.get_columns("tax_optimiser_review_session")}
        if "advisor_user_id" in cols:
            print("tax_optimiser_review_session.advisor_user_id already exists")
            return
        db.session.execute(
            text(
                """
                ALTER TABLE tax_optimiser_review_session
                ADD COLUMN advisor_user_id INT NOT NULL DEFAULT 0
                AFTER created_by_user_id
                """
            )
        )
        db.session.execute(
            text(
                """
                UPDATE tax_optimiser_review_session
                SET advisor_user_id = created_by_user_id
                WHERE advisor_user_id = 0
                """
            )
        )
        db.session.commit()
        print("Added tax_optimiser_review_session.advisor_user_id and backfilled from created_by_user_id")


if __name__ == "__main__":
    upgrade()
