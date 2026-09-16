#!/usr/bin/env python3
"""
Migration: Add task_assignment_rule table and assigned_to column to data_integrity_issue.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text, inspect


def _table_exists(engine, name):
    inspector = inspect(engine)
    return name in inspector.get_table_names()


def _column_exists(engine, table_name, column_name):
    inspector = inspect(engine)
    columns = [c['name'] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade():
    """Create task_assignment_rule table and add assigned_to to data_integrity_issue."""
    app = create_app()
    with app.app_context():
        try:
            # Create task_assignment_rule table
            if not _table_exists(db.engine, 'task_assignment_rule'):
                db.session.execute(text("""
                    CREATE TABLE task_assignment_rule (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        rule_type VARCHAR(50) NOT NULL,
                        match_value VARCHAR(100) NOT NULL,
                        assigned_user_id INT NOT NULL,
                        priority INT DEFAULT 0,
                        is_active TINYINT(1) DEFAULT 1,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (assigned_user_id) REFERENCES user(id) ON DELETE CASCADE,
                        INDEX ix_task_assignment_rule_type (rule_type),
                        INDEX ix_task_assignment_rule_active (is_active)
                    )
                """))
                db.session.commit()
                print("Created task_assignment_rule table")
            else:
                print("task_assignment_rule table already exists")

            # Add assigned_to column to data_integrity_issue if not exists
            if not _column_exists(db.engine, 'data_integrity_issue', 'assigned_to'):
                db.session.execute(text("""
                    ALTER TABLE data_integrity_issue
                    ADD COLUMN assigned_to INT NULL,
                    ADD INDEX ix_data_integrity_issue_assigned_to (assigned_to),
                    ADD CONSTRAINT fk_data_integrity_issue_assigned_to
                    FOREIGN KEY (assigned_to) REFERENCES user(id) ON DELETE SET NULL
                """))
                db.session.commit()
                print("Added assigned_to column to data_integrity_issue")
            else:
                print("assigned_to column already exists in data_integrity_issue")

        except Exception as e:
            db.session.rollback()
            print(f"Error: {e}")
            raise


if __name__ == '__main__':
    upgrade()
