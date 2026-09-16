#!/usr/bin/env python3
"""
Migration: Google Tasks integration for OpsTask assignees.

- ops_task.google_tasks_task_id: remote task id on assignee default list
- ops_task.google_tasks_sync_user_id: user whose OAuth token created the task
"""
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
        eng = db.engine
        try:
            if not _column_exists(eng, "ops_task", "google_tasks_task_id"):
                db.session.execute(
                    text(
                        "ALTER TABLE ops_task ADD COLUMN google_tasks_task_id VARCHAR(280) NULL"
                    )
                )
                db.session.commit()
                print("Added ops_task.google_tasks_task_id")
            else:
                print("ops_task.google_tasks_task_id already exists")

            if not _column_exists(eng, "ops_task", "google_tasks_sync_user_id"):
                db.session.execute(
                    text(
                        "ALTER TABLE ops_task ADD COLUMN google_tasks_sync_user_id INT NULL"
                    )
                )
                db.session.commit()
                db.session.execute(
                    text(
                        "ALTER TABLE ops_task ADD CONSTRAINT ops_task_gtasks_sync_user_fk "
                        "FOREIGN KEY (google_tasks_sync_user_id) REFERENCES user(id) ON DELETE SET NULL"
                    )
                )
                db.session.commit()
                print("Added ops_task.google_tasks_sync_user_id")
            else:
                print("ops_task.google_tasks_sync_user_id already exists")
        except Exception as e:
            db.session.rollback()
            print(f"Error: {e}")
            raise


if __name__ == "__main__":
    upgrade()
