#!/usr/bin/env python3
"""Create finding_notification_decision table if missing.

Run: python migrations/add_finding_notification_decision.py

Listed in docs/DB_CUTOVER_REGISTRY.md. Gate: services/db_cutover.py
finding_notification_enabled().
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def main():
    app = create_app()
    with app.app_context():
        from models.finding_notification_decision import FindingNotificationDecision
        import services.db_cutover as cutover

        FindingNotificationDecision.__table__.create(db.engine, checkfirst=True)
        cutover._finding_notif_table_checked = None
        print("finding_notification_decision table ready")
        print("finding_notification_enabled =", cutover.finding_notification_enabled())


if __name__ == "__main__":
    main()
