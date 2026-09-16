#!/usr/bin/env python3
"""
Close duplicate alerts by marking duplicates as resolved with a standard "duplicate" note.

Primary use-case:
  Alerts dashboard shows the same client for the same workflow stage (alert_subtype),
  typically for workflow_sla alerts. This script keeps ONE alert per (client_id, alert_type, alert_subtype)
  and resolves the rest as duplicates.

Safe by default:
  - Defaults to --dry-run (no writes) unless --apply is provided.
  - Only targets alerts with status in: active, acknowledged, snoozed (configurable).

Examples:
  python scripts/close_duplicate_alerts.py --dry-run
  python scripts/close_duplicate_alerts.py --apply --user-id 1
  python scripts/close_duplicate_alerts.py --apply --alert-type workflow_sla --keep newest
"""

import argparse
import os
import sys
from collections import defaultdict

# Add app root to sys.path (scripts/..)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app  # noqa: E402
from models import db  # noqa: E402
from alert_system_models import Alert  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Resolve duplicate alerts as 'duplicate'.")
    mode = p.add_mutually_exclusive_group(required=False)
    mode.add_argument("--dry-run", action="store_true", help="Show what would change (default).")
    mode.add_argument("--apply", action="store_true", help="Apply changes (commit to DB).")

    p.add_argument(
        "--alert-type",
        default="workflow_sla",
        help="Only consider alerts of this type (default: workflow_sla). Use 'all' to disable.",
    )
    p.add_argument(
        "--statuses",
        default="active,acknowledged,snoozed",
        help="Comma-separated statuses to consider as 'open' for dedupe (default: active,acknowledged,snoozed).",
    )
    p.add_argument(
        "--keep",
        choices=["newest", "oldest"],
        default="newest",
        help="Which alert to keep in each duplicate group (default: newest).",
    )
    p.add_argument(
        "--user-id",
        type=int,
        default=1,
        help="User ID to record as resolver (default: 1).",
    )
    p.add_argument(
        "--max-groups",
        type=int,
        default=0,
        help="Safety limit: max duplicate groups to process (0 = no limit).",
    )
    p.add_argument(
        "--max-resolves",
        type=int,
        default=0,
        help="Safety limit: max number of alerts to resolve (0 = no limit).",
    )
    return p.parse_args()


def _key_for_alert(alert: Alert):
    # "Same client for same stage" maps to (client_id + alert_subtype) for workflow_sla.
    # We include alert_type to avoid cross-type collisions.
    return (alert.client_id, alert.alert_type, alert.alert_subtype)


def close_duplicates(args: argparse.Namespace) -> dict:
    app = create_app()
    dry_run = not args.apply  # default to dry-run unless explicitly applying
    statuses = [s.strip() for s in (args.statuses or "").split(",") if s.strip()]

    with app.app_context():
        q = Alert.query.filter(Alert.status.in_(statuses))
        if args.alert_type and args.alert_type.lower() != "all":
            q = q.filter(Alert.alert_type == args.alert_type)

        # Ignore alerts that can't participate in client+stage dedupe
        q = q.filter(Alert.client_id.isnot(None), Alert.alert_subtype.isnot(None))

        alerts = q.order_by(Alert.created_at.desc()).all()

        groups = defaultdict(list)
        for a in alerts:
            groups[_key_for_alert(a)].append(a)

        duplicate_groups = [(k, v) for (k, v) in groups.items() if len(v) > 1]
        duplicate_groups.sort(key=lambda kv: len(kv[1]), reverse=True)

        processed_groups = 0
        resolved_count = 0

        for (key, items) in duplicate_groups:
            if args.max_groups and processed_groups >= args.max_groups:
                break

            # Determine keep candidate
            if args.keep == "newest":
                items_sorted = sorted(items, key=lambda a: a.created_at or 0, reverse=True)
            else:
                items_sorted = sorted(items, key=lambda a: a.created_at or 0)

            keep = items_sorted[0]
            to_resolve = [a for a in items_sorted[1:]]

            if not to_resolve:
                continue

            processed_groups += 1

            for a in to_resolve:
                if args.max_resolves and resolved_count >= args.max_resolves:
                    break

                note = (
                    f"Duplicate alert closed by script: duplicate of alert {keep.id} "
                    f"(kept). Key=(client_id={keep.client_id}, type={keep.alert_type}, subtype={keep.alert_subtype})."
                )
                if dry_run:
                    print(
                        f"[DRY-RUN] Would resolve alert {a.id} as duplicate of {keep.id} "
                        f"(client_id={a.client_id}, type={a.alert_type}, subtype={a.alert_subtype}, status={a.status})"
                    )
                else:
                    a.resolve(args.user_id, note)
                    resolved_count += 1

            if not dry_run and resolved_count:
                db.session.commit()

        if not dry_run:
            # Ensure no pending transaction
            db.session.commit()

        return {
            "dry_run": dry_run,
            "candidate_alerts_scanned": len(alerts),
            "duplicate_groups_found": len(duplicate_groups),
            "duplicate_groups_processed": processed_groups,
            "alerts_resolved": resolved_count if not dry_run else 0,
        }


if __name__ == "__main__":
    args = parse_args()
    result = close_duplicates(args)
    print("\nSummary:")
    for k, v in result.items():
        print(f"  {k}: {v}")



