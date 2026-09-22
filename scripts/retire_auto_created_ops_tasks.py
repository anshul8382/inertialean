#!/usr/bin/env python
"""
One-off: complete auto-created OpsTasks that mirror a ReviewWorkflow or DataIntegrityIssue.

Policy (2026-09): no machine-created OpsTasks. The open review / finding is the work item;
users may still create tasks for their own tracking. This script retires the backlog that
was created before that policy.

Completes a task only when ALL of these hold:
  * status in (pending, in_progress, snoozed)
  * notes start with "Auto-created from ReviewWorkflow #"
    OR "Auto-created from DataIntegrityIssue #"

User-created tasks (no such notes prefix) are never touched.

Usage:
  python scripts/retire_auto_created_ops_tasks.py              # dry run (default)
  python scripts/retire_auto_created_ops_tasks.py --apply      # complete matching tasks
  python scripts/retire_auto_created_ops_tasks.py --reviews-only
  python scripts/retire_auto_created_ops_tasks.py --findings-only

KVM runbook: docs/PROD_DATA_CLEANUP_SCRIPTS.md § 3.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extensions import db
from main import create_app
from models import OpsTask

OPEN_STATUSES = ["pending", "in_progress", "snoozed"]
REVIEW_PREFIX = "Auto-created from ReviewWorkflow #"
FINDING_PREFIX = "Auto-created from DataIntegrityIssue #"
CLOSE_NOTE = "[Auto-closed: machine-created mirror retired — open review/finding is the work item]"


def _is_auto(task: OpsTask, *, reviews: bool, findings: bool) -> bool:
    notes = (task.notes or "").lstrip()
    if reviews and notes.startswith(REVIEW_PREFIX):
        return True
    if findings and notes.startswith(FINDING_PREFIX):
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Complete auto-created OpsTask mirrors (dry run unless --apply)."
    )
    parser.add_argument("--apply", action="store_true", help="Commit completions.")
    parser.add_argument("--reviews-only", action="store_true")
    parser.add_argument("--findings-only", action="store_true")
    parser.add_argument("--limit", type=int, default=10, help="Sample rows to print.")
    args = parser.parse_args()

    reviews = not args.findings_only
    findings = not args.reviews_only
    if args.reviews_only and args.findings_only:
        parser.error("Use only one of --reviews-only / --findings-only")

    app = create_app()
    with app.app_context():
        open_tasks = OpsTask.query.filter(OpsTask.status.in_(OPEN_STATUSES)).all()
        to_close = [t for t in open_tasks if _is_auto(t, reviews=reviews, findings=findings)]

        by_kind = {"review": 0, "finding": 0}
        for t in to_close:
            notes = (t.notes or "").lstrip()
            if notes.startswith(REVIEW_PREFIX):
                by_kind["review"] += 1
            elif notes.startswith(FINDING_PREFIX):
                by_kind["finding"] += 1

        print(f"Open OpsTasks (all):                 {len(open_tasks)}")
        print(f"Auto-created to COMPLETE:            {len(to_close)}")
        print(f"  from ReviewWorkflow:               {by_kind['review']}")
        print(f"  from DataIntegrityIssue:           {by_kind['finding']}")
        print(f"Left alone (user / other):           {len(open_tasks) - len(to_close)}")

        if not to_close:
            print("\nNothing to complete.")
            return

        print("\nSample:")
        for t in to_close[: args.limit]:
            kind = "review" if (t.notes or "").lstrip().startswith(REVIEW_PREFIX) else "finding"
            print(f"  task #{t.id} [{kind}] {t.name}")
        if len(to_close) > args.limit:
            print(f"  … {len(to_close) - args.limit} more")

        if not args.apply:
            print("\nDRY RUN — nothing changed. Re-run with --apply to complete.")
            return

        now = datetime.utcnow()
        for t in to_close:
            t.status = "completed"
            t.completed_at = now
            # completed_by left NULL — machine retirement, not a user close
            t.notes = f"{t.notes}\n{CLOSE_NOTE}" if t.notes else CLOSE_NOTE

        db.session.commit()
        print(f"\nCompleted {len(to_close)} auto-created OpsTasks.")

        remaining_open = OpsTask.query.filter(OpsTask.status.in_(OPEN_STATUSES)).count()
        remaining_auto = sum(
            1
            for t in OpsTask.query.filter(OpsTask.status.in_(OPEN_STATUSES)).all()
            if _is_auto(t, reviews=True, findings=True)
        )
        print(f"Open OpsTasks now: {remaining_open} (auto-created still open: {remaining_auto})")


if __name__ == "__main__":
    main()
