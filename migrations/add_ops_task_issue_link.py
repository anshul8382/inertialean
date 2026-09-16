#!/usr/bin/env python3
"""
Migration: Add data_integrity_issue_id to ops_task for linking auto-created tasks.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text, inspect


def _column_exists(engine, table_name, column_name):
    inspector = inspect(engine)
    columns = [c['name'] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade():
    app = create_app()
    with app.app_context():
        try:
            if not _column_exists(db.engine, 'ops_task', 'data_integrity_issue_id'):
                db.session.execute(text("""
                    ALTER TABLE ops_task
                    ADD COLUMN data_integrity_issue_id INT NULL,
                    ADD INDEX ix_ops_task_data_integrity_issue_id (data_integrity_issue_id),
                    ADD CONSTRAINT fk_ops_task_data_integrity_issue
                    FOREIGN KEY (data_integrity_issue_id) REFERENCES data_integrity_issue(id) ON DELETE SET NULL
                """))
                db.session.commit()
                print("Added data_integrity_issue_id to ops_task")
            else:
                print("data_integrity_issue_id already exists in ops_task")
        except Exception as e:
            db.session.rollback()
            print(f"Error: {e}")
            raise


if __name__ == '__main__':
    upgrade()
