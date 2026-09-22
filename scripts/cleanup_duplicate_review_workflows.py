#!/usr/bin/env python
"""
One-off: enforce "one open review per client" by deleting duplicate review rows left behind
by the old 24-month horizon generator.

Background: sync_missing_workflows() used to pre-create a full 24-month horizon of reviews.
It was later fixed to create only the next single open review, but the rows it already made
were never cleaned up. They age, clutter the pendency reports, and mirror into OpsTasks.

Deletes a review only when ALL of these hold:
  * status is not 'closed'
  * it is NOT the keeper for that client (keeper = earliest review_date, lowest id on ties)
  * status == 'initiated'          — still at the first stage, never advanced
  * notes / meeting_notes are empty and meeting_date is NULL  — no record of any real work

Anything carrying notes, meeting notes or a meeting date is evidence of real work and is
reported for a human to look at, never deleted. Closed reviews are never touched: they are
the review history that C1's annual floor is measured from.

Deletion (not cancellation) is deliberate — these rows correspond to no client event, and
'cancelled' is not part of the review vocabulary. Marking them 'closed' would fabricate a
review history and reset the annual clock.

Linked OpsTasks are completed first: ops_task.review_workflow_id is ON DELETE SET NULL, so
deleting a review would otherwise leave orphaned pending tasks with no link back.

Usage:
  python scripts/cleanup_duplicate_review_workflows.py            # dry run (default)
  python scripts/cleanup_duplicate_review_workflows.py --apply    # actually delete
  python scripts/cleanup_duplicate_review_workflows.py --limit 5  # sample output size

KVM runbook (dry-run first, then --apply): docs/PROD_DATA_CLEANUP_SCRIPTS.md § 1.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from extensions import db
from main import create_app
from models import OpsTask, ReviewWorkflow

OPEN_TASK_STATUSES = ["pending", "in_progress", "snoozed"]
FIRST_STAGE = "initiated"


def _blank(value) -> bool:
    return value is None or not str(value).strip()


def _is_safe_to_delete(review: ReviewWorkflow) -> bool:
    """Untouched first-stage row with no record of any work having happened."""
    return (
        (review.status or "").strip().lower() == FIRST_STAGE
        and _blank(review.notes)
        and _blank(review.meeting_notes)
        and review.meeting_date is None
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete duplicate open review workflows (dry run unless --apply)."
    )
    parser.add_argument(
        "--apply", action="store_true", help="Commit the deletions. Without this, nothing changes."
    )
    parser.add_argument("--limit", type=int, default=10, help="Rows to show per sample list.")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        open_reviews = (
            ReviewWorkflow.query.filter(ReviewWorkflow.status != "closed")
            .order_by(ReviewWorkflow.client_id, ReviewWorkflow.review_date, ReviewWorkflow.id)
            .all()
        )

        by_client = defaultdict(list)
        for review in open_reviews:
            by_client[review.client_id].append(review)

        keepers, to_delete, needs_human = [], [], []
        for client_id, reviews in by_client.items():
            # Deterministic keeper: earliest review_date, lowest id on ties.
            ordered = sorted(reviews, key=lambda r: (r.review_date, r.id))
            keepers.append(ordered[0])
            for review in ordered[1:]:
                (to_delete if _is_safe_to_delete(review) else needs_human).append(review)

        print(f"Open reviews (status != 'closed'): {len(open_reviews)}")
        print(f"Clients with an open review:       {len(by_client)}")
        dupes = sum(1 for reviews in by_client.values() if len(reviews) > 1)
        print(f"Clients with MORE than one open:   {dupes}")
        print()
        print(f"Keep (one per client):             {len(keepers)}")
        print(f"DELETE (first stage, no record):   {len(to_delete)}")
        print(f"Skip — carries notes/meeting:      {len(needs_human)}")

        if needs_human:
            print()
            print("  These are duplicates that carry evidence of real work. They are NOT deleted.")
            print("  The one-open-review-per-client invariant is not reached until a human")
            print("  resolves them (close them, or confirm which is the live review):")
            for review in needs_human[: args.limit]:
                marks = []
                if not _blank(review.notes):
                    marks.append("notes")
                if not _blank(review.meeting_notes):
                    marks.append("meeting_notes")
                if review.meeting_date is not None:
                    marks.append("meeting_date")
                if (review.status or "").strip().lower() != FIRST_STAGE:
                    marks.append(f"status={review.status}")
                print(
                    f"    review #{review.id} client={review.client_id} "
                    f"due={review.review_date} [{', '.join(marks)}]"
                )
            if len(needs_human) > args.limit:
                print(f"    … {len(needs_human) - args.limit} more")

        if not to_delete:
            print("\nNothing to delete.")
            return

        delete_ids = [review.id for review in to_delete]
        mirror_tasks = OpsTask.query.filter(
            OpsTask.review_workflow_id.in_(delete_ids),
            OpsTask.status.in_(OPEN_TASK_STATUSES),
        ).all()

        print()
        print(f"Open OpsTasks mirroring deleted reviews: {len(mirror_tasks)}")
        print("  (completed first — review_workflow_id is ON DELETE SET NULL, so these would")
        print("   otherwise be left pending with no link back to anything)")

        print()
        print("Sample of rows to delete:")
        for review in to_delete[: args.limit]:
            print(
                f"    review #{review.id} client={review.client_id} "
                f"due={review.review_date} status={review.status}"
            )
        if len(to_delete) > args.limit:
            print(f"    … {len(to_delete) - args.limit} more")

        if not args.apply:
            print()
            print("DRY RUN — nothing changed. Re-run with --apply to delete.")
            return

        now = datetime.utcnow()
        for task in mirror_tasks:
            task.status = "completed"
            task.completed_at = now
            note = "[Auto-closed: duplicate review row removed by horizon cleanup]"
            task.notes = f"{task.notes}\n{note}" if task.notes else note

        for review in to_delete:
            db.session.delete(review)

        db.session.commit()
        print()
        print(f"Deleted {len(to_delete)} review rows; completed {len(mirror_tasks)} linked tasks.")

        remaining = (
            ReviewWorkflow.query.filter(ReviewWorkflow.status != "closed").all()
        )
        still_dupe = defaultdict(int)
        for review in remaining:
            still_dupe[review.client_id] += 1
        offenders = sum(1 for count in still_dupe.values() if count > 1)
        print(f"Open reviews now: {len(remaining)} across {len(still_dupe)} clients.")
        print(f"Clients still holding more than one open review: {offenders}")


if __name__ == "__main__":
    main()
