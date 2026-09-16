#!/usr/bin/env python3
"""Add ops_task.google_calendar_reminder_event_id for separate Google Reminder entries."""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import inspect, text


def _column_exists(engine, table: str, column: str) -> bool:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    app = create_app()
    with app.app_context():
        try:
            if not _column_exists(db.engine, "ops_task", "google_calendar_reminder_event_id"):
                db.session.execute(
                    text(
                        "ALTER TABLE ops_task ADD COLUMN google_calendar_reminder_event_id VARCHAR(280) NULL"
                    )
                )
                db.session.commit()
                print("Added ops_task.google_calendar_reminder_event_id")
            else:
                print("ops_task.google_calendar_reminder_event_id already exists")
        except Exception as e:
            db.session.rollback()
            print(f"Error: {e}")
            raise


if __name__ == "__main__":
    upgrade()
