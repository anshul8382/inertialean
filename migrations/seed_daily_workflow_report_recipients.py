#!/usr/bin/env python3
"""
Ensure daily workflow report recipients exist in report_recipient.

Idempotent: skips rows that already exist for job_id + email (case-insensitive).

Run: python migrations/seed_daily_workflow_report_recipients.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from models import ReportRecipient


JOB_ID = "daily_workflow_report"
# (email, name) — use full domain addresses used by mail systems
RECIPIENTS = [
    ("anshul@equities4wealth.com", "Anshul"),
    ("sharveen@equities4wealth.com", "Sharveen"),
]


def seed():
    app = create_app()
    with app.app_context():
        existing_lower = {
            (r.email or "").strip().lower()
            for r in ReportRecipient.query.filter_by(job_id=JOB_ID).all()
        }
        added = 0
        for email, name in RECIPIENTS:
            key = email.strip().lower()
            if key in existing_lower:
                print(f"  Skip (exists): {email}")
                continue
            db.session.add(
                ReportRecipient(
                    job_id=JOB_ID,
                    email=email,
                    name=name,
                    is_active=True,
                )
            )
            existing_lower.add(key)
            added += 1
            print(f"  Added: {email} ({name})")
        db.session.commit()
        print(f"Done: {added} recipient(s) inserted for {JOB_ID}.")


if __name__ == "__main__":
    seed()
