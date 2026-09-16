#!/usr/bin/env python3
"""
Migration: Add assigned_to to review_workflow for task assignment.
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
            if not _column_exists(db.engine, 'review_workflow', 'assigned_to'):
                db.session.execute(text("""
                    ALTER TABLE review_workflow
                    ADD COLUMN assigned_to INT NULL,
                    ADD INDEX ix_review_workflow_assigned_to (assigned_to),
                    ADD CONSTRAINT fk_review_workflow_assigned_to
                    FOREIGN KEY (assigned_to) REFERENCES user(id) ON DELETE SET NULL
                """))
                db.session.commit()
                print("Added assigned_to to review_workflow")
            else:
                print("assigned_to already exists in review_workflow")
        except Exception as e:
            db.session.rollback()
            print(f"Error: {e}")
            raise


if __name__ == '__main__':
    upgrade()
