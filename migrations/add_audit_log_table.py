#!/usr/bin/env python3
"""Create audit_log table if missing. Run: python migrations/add_audit_log_table.py

Listed in docs/DB_CUTOVER_REGISTRY.md. While deferred, set DEFER_DB_FEATURES=audit_log in .env.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def main():
    app = create_app()
    with app.app_context():
        from models.audit_log import AuditLog

        AuditLog.__table__.create(db.engine, checkfirst=True)
        print("audit_log table ready")


if __name__ == "__main__":
    main()
