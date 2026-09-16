#!/usr/bin/env python3
"""
One-shot backfill: push all open ops tasks and active meetings to Google Calendar,
and open ops tasks to Google Tasks (assignee default list).

Uses the same logic as the web app (deadline + reminder events for tasks; meetings with
attendees; Tasks API for ops tasks). Only processes rows where someone has connected Google
(assignee for tasks; calendar host for meetings).

Usage (from repo root):
  python scripts/sync_google_calendar_backfill.py
  python scripts/sync_google_calendar_backfill.py --dry-run
  python scripts/sync_google_calendar_backfill.py --sleep 0.25

Requires .env with GOOGLE_CALENDAR_CLIENT_ID / SECRET and DB credentials.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from main import create_app
from models import Meeting, MeetingParticipant, OpsTask, User
from sqlalchemy.orm import selectinload

OPEN_TASK_STATUSES = ("pending", "in_progress", "snoozed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Pause between Google API calls (seconds) to reduce rate-limit risk",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only; do not call Google",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        cid = (app.config.get("GOOGLE_CALENDAR_CLIENT_ID") or "").strip()
        csec = (app.config.get("GOOGLE_CALENDAR_CLIENT_SECRET") or "").strip()
        if not cid or not csec:
            print("ERROR: GOOGLE_CALENDAR_CLIENT_ID / GOOGLE_CALENDAR_CLIENT_SECRET not set.")
            return 1

        from services import google_calendar_service as gcs
        from services import google_tasks_service as gts

        with app.test_request_context("/"):
            if not gcs.is_oauth_configured():
                print("ERROR: Calendar OAuth not configured in this process.")
                return 1

            tasks = (
                OpsTask.query.filter(
                    OpsTask.status.in_(OPEN_TASK_STATUSES),
                    OpsTask.assigned_to.isnot(None),
                )
                .order_by(OpsTask.id)
                .all()
            )
            meetings = (
                Meeting.query.options(
                    selectinload(Meeting.participants_assoc).selectinload(
                        MeetingParticipant.user
                    )
                )
                .filter(~Meeting.status.in_(["cancelled", "completed"]))
                .order_by(Meeting.id)
                .all()
            )

            tasks_with_token = 0
            for t in tasks:
                u = User.query.get(t.assigned_to)
                if u and getattr(u, "google_calendar_refresh_token", None):
                    tasks_with_token += 1

            meetings_syncable = 0
            for m in meetings:
                if gcs._meeting_calendar_organizer(m) is not None:
                    meetings_syncable += 1

            print(f"Open ops tasks (assigned): {len(tasks)}")
            print(f"  …assignee has Google connected: {tasks_with_token}")
            print(f"Active meetings (not cancelled/completed): {len(meetings)}")
            print(f"  …calendar host can sync: {meetings_syncable}")

            if args.dry_run:
                print("Dry run — no API calls made.")
                return 0

            ok_t = skip_t = err_t = 0
            for t in tasks:
                u = User.query.get(t.assigned_to)
                if not u or not getattr(u, "google_calendar_refresh_token", None):
                    skip_t += 1
                    continue
                try:
                    gcs.sync_ops_task_google_calendar(t.id)
                    gts.sync_ops_task_google_tasks(t.id)
                    ok_t += 1
                except Exception as e:
                    print(f"ERROR ops_task id={t.id}: {e}")
                    err_t += 1
                time.sleep(args.sleep)

            ok_m = skip_m = err_m = 0
            for m in meetings:
                if gcs._meeting_calendar_organizer(m) is None:
                    skip_m += 1
                    continue
                try:
                    gcs.sync_meeting_google_calendar(m.id)
                    ok_m += 1
                except Exception as e:
                    print(f"ERROR meeting id={m.id}: {e}")
                    err_m += 1
                time.sleep(args.sleep)

            print(
                f"Done. Tasks: synced={ok_t} skipped(no assignee token)={skip_t} errors={err_t} | "
                f"Meetings: synced={ok_m} skipped(no host token)={skip_m} errors={err_m}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
