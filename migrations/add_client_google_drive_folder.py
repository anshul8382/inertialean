#!/usr/bin/env python3
"""Add google_drive_folder_id / google_drive_folder_note on client.

Run: python3 migrations/add_client_google_drive_folder.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from extensions import db
from main import create_app

TABLE = "client"
COLUMNS = {
    "google_drive_folder_id": "VARCHAR(128) NULL",
    "google_drive_folder_note": "VARCHAR(255) NULL",
}


def main():
    app = create_app()
    with app.app_context():
        cols = {c["name"] for c in inspect(db.engine).get_columns(TABLE)}
        for name, ddl in COLUMNS.items():
            if name in cols:
                print(f"OK {TABLE}.{name} already exists")
                continue
            db.session.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {name} {ddl}"))
            db.session.commit()
            print(f"Added {TABLE}.{name}")

        # suitability_report table
        from models.suitability_report import SuitabilityReport

        SuitabilityReport.__table__.create(db.engine, checkfirst=True)
        print("suitability_report table ready")


if __name__ == "__main__":
    main()
