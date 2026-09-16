#!/usr/bin/env python3
"""
Add client_id to tax_optimiser_review_session if missing.
0 = session started for all clients in FY; else matches start_payload.client_id.
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
        if "client_id" in cols:
            print("tax_optimiser_review_session.client_id already exists")
            return
        db.session.execute(
            text(
                """
                ALTER TABLE tax_optimiser_review_session
                ADD COLUMN client_id INT NOT NULL DEFAULT 0
                """
            )
        )
        db.session.commit()
        print("Added tax_optimiser_review_session.client_id (default 0)")


if __name__ == "__main__":
    upgrade()
