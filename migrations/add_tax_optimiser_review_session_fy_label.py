#!/usr/bin/env python3
"""Add fy_label to tax_optimiser_review_session if missing (denormalized for listing/filtering)."""
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
        if "fy_label" in cols:
            print("tax_optimiser_review_session.fy_label already exists")
            return
        db.session.execute(
            text(
                """
                ALTER TABLE tax_optimiser_review_session
                ADD COLUMN fy_label VARCHAR(64) NOT NULL DEFAULT ''
                """
            )
        )
        db.session.commit()
        print("Added tax_optimiser_review_session.fy_label")


if __name__ == "__main__":
    upgrade()
