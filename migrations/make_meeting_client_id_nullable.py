#!/usr/bin/env python3
"""
Allow meetings with a lead only (no client yet): meeting.client_id NULL.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import inspect, text


def _column_nullable(engine, table: str, column: str) -> bool:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return False
    for c in insp.get_columns(table):
        if c["name"] == column:
            return bool(c.get("nullable", True))
    return False


def upgrade():
    app = create_app()
    with app.app_context():
        try:
            if _column_nullable(db.engine, "meeting", "client_id"):
                print("meeting.client_id is already nullable")
                return
            db.session.execute(text("ALTER TABLE meeting MODIFY client_id INT NULL"))
            db.session.commit()
            print("meeting.client_id is now nullable")
        except Exception as e:
            db.session.rollback()
            print(f"Error: {e}")
            raise


if __name__ == "__main__":
    upgrade()
