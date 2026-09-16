#!/usr/bin/env python3
"""
Migration: Add ops_task table for task management.
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


def upgrade():
    """Create ops_task table."""
    app = create_app()
    with app.app_context():
        try:
            if not _table_exists(db.engine, 'ops_task'):
                db.session.execute(text("""
                    CREATE TABLE ops_task (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(200) NOT NULL,
                        deadline DATETIME NOT NULL,
                        assigned_to INT NULL,
                        notes TEXT NULL,
                        priority VARCHAR(20) DEFAULT 'medium',
                        status VARCHAR(20) NOT NULL DEFAULT 'pending',
                        pre_snooze_status VARCHAR(20) NULL,
                        snoozed_until DATETIME NULL,
                        snoozed_at DATETIME NULL,
                        snoozed_by INT NULL,
                        snooze_reason TEXT NULL,
                        reminder_at DATETIME NULL,
                        reminder_sent TINYINT(1) DEFAULT 0,
                        client_id INT NULL,
                        created_by INT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        completed_at DATETIME NULL,
                        completed_by INT NULL,
                        FOREIGN KEY (assigned_to) REFERENCES user(id) ON DELETE SET NULL,
                        FOREIGN KEY (snoozed_by) REFERENCES user(id) ON DELETE SET NULL,
                        FOREIGN KEY (created_by) REFERENCES user(id),
                        FOREIGN KEY (completed_by) REFERENCES user(id) ON DELETE SET NULL,
                        FOREIGN KEY (client_id) REFERENCES client(id) ON DELETE SET NULL,
                        INDEX ix_ops_task_assigned_to (assigned_to),
                        INDEX ix_ops_task_status (status),
                        INDEX ix_ops_task_deadline (deadline),
                        INDEX ix_ops_task_reminder_at (reminder_at)
                    )
                """))
                db.session.commit()
                print("Created ops_task table")
            else:
                print("ops_task table already exists")
        except Exception as e:
            db.session.rollback()
            print(f"Error: {e}")
            raise


if __name__ == '__main__':
    upgrade()
