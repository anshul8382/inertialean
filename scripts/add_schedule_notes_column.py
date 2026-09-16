#!/usr/bin/env python3
"""Add schedule_notes column to workflow table. Run from app root: python scripts/add_schedule_notes_column.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import create_app
from extensions import db
from sqlalchemy import text

def main():
    app = create_app()
    with app.app_context():
        result = db.session.execute(text("SHOW COLUMNS FROM workflow LIKE 'schedule_notes'"))
        if result.fetchone():
            print("schedule_notes column already exists")
            return 0
        db.session.execute(text("ALTER TABLE workflow ADD COLUMN schedule_notes TEXT NULL"))
        db.session.commit()
        print("Added schedule_notes column to workflow table")
        return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
