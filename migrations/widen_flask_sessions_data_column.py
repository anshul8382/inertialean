#!/usr/bin/env python3
"""
Widen Flask-Session `sessions.data` from BLOB (64KB) to MEDIUMBLOB (16MB).

Editing sent recommendations on /security-distribution stores pending state in
Flask-Session. Section lists + holdings often exceed 64KB and raise:

  DataError: (1406, "Data too long for column 'data' at row 1")

Run:
  python migrations/widen_flask_sessions_data_column.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import create_app
from extensions import db


def main() -> int:
    app = create_app()
    with app.app_context():
        row = db.session.execute(db.text("SHOW COLUMNS FROM sessions LIKE 'data'")).fetchone()
        if not row:
            print("sessions.data column not found — skip")
            return 1
        col_type = (row[1] or "").lower()
        print(f"Current sessions.data type: {col_type}")
        if "mediumblob" in col_type or "longblob" in col_type:
            print("Already wide enough — nothing to do")
            return 0
        db.session.execute(db.text("ALTER TABLE sessions MODIFY COLUMN data MEDIUMBLOB"))
        db.session.commit()
        after = db.session.execute(db.text("SHOW COLUMNS FROM sessions LIKE 'data'")).fetchone()
        print(f"Updated sessions.data type: {after[1] if after else '?'}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
