#!/usr/bin/env python3
"""Create advisory_register_entry table if missing.

Run: python3 migrations/add_advisory_register_entry.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def main():
    app = create_app()
    with app.app_context():
        from models.advisory_register import AdvisoryRegisterEntry
        import services.db_cutover as cutover

        AdvisoryRegisterEntry.__table__.create(db.engine, checkfirst=True)
        cutover._advisory_register_table_checked = None
        print("advisory_register_entry table ready")
        print("advisory_register_enabled =", cutover.advisory_register_enabled())


if __name__ == "__main__":
    main()
