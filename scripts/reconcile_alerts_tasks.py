#!/usr/bin/env python
"""
Reconcile alerts and OpsTasks with current issue state, then optionally refresh pipeline.

Closes issue-linked tasks when issues are resolved/not open (or obsolete per workflow).
Resolves alerts when every linked issue is non-open, or when client has no open issues
(agent_orchestrated / data_integrity / recommendation_execution).
Clears alert_id on non-open issues for a clean orchestrator attach.
Runs workflow SLA stale backfill (same as backfill_workflow_stage_alerts_and_tasks.py).
Runs task auto-close, then optionally TaskAssignmentAgent backfill + AlertOrchestrator.

Usage:
  python scripts/reconcile_alerts_tasks.py --dry-run
  python scripts/reconcile_alerts_tasks.py
  python scripts/reconcile_alerts_tasks.py --refresh
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(
        description="Reconcile alerts/tasks with issues; optional orchestrator refresh"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze only; no database writes (workflow counts are alert-only)",
    )
    parser.add_argument(
        "--no-workflow-backfill",
        action="store_true",
        help="Skip stale workflow_sla + per-workflow task/issue closure",
    )
    parser.add_argument(
        "--no-task-auto-close",
        action="store_true",
        help="Skip close_completed_ops_tasks at end (not recommended)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="After reconcile: TaskAssignmentAgent backfill + AlertOrchestrator (writes)",
    )
    parser.add_argument("--json", action="store_true", help="Print stats as JSON only")
    args = parser.parse_args()

    from main import create_app
    from services.alert_task_reconcile_service import run_full_reconcile

    app = create_app()
    with app.app_context():
        stats = run_full_reconcile(
            dry_run=args.dry_run,
            workflow_backfill=not args.no_workflow_backfill,
            run_task_auto_close=not args.no_task_auto_close,
            refresh_assignment_and_alerts=args.refresh and not args.dry_run,
        )
        if args.json:
            print(json.dumps(stats, default=str))
        else:
            print("=== Reconcile results ===")
            for k, v in stats.items():
                print(f"  {k}: {v}")
            if args.dry_run:
                print("\n(DRY RUN — no changes committed except none)")
            if args.refresh and args.dry_run:
                print("\n(--refresh ignored in dry-run)")


if __name__ == "__main__":
    main()
