#!/usr/bin/env python
"""
One-time backfill: close stale workflow_sla alerts and obsolete OpsTasks/issues
for workflows that have already moved to a later stage.

Run once after deploying workflow_stage_service so that:
- Any workflow_sla alerts for stages the workflow has left are resolved.
- OpsTasks and DataIntegrityIssues tied to those workflows are closed/resolved
  when the workflow has already progressed past the condition.

Usage:
  python scripts/backfill_workflow_stage_alerts_and_tasks.py
  python scripts/backfill_workflow_stage_alerts_and_tasks.py --dry-run  # no commits
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stage order: stages before current_stage are "stale"
STAGE_ORDER = {'FUNDS': 0, 'RECOS': 1, 'NOTIFY': 2, 'EXEC': 3, 'UPDATE': 4, 'COMPLETED': 5}


def backfill(dry_run=False):
    from main import create_app
    from models import Workflow

    app = create_app()
    with app.app_context():
        workflows = Workflow.query.filter(Workflow.current_stage.isnot(None)).all()
        total_alerts = 0
        total_tasks = 0
        total_issues = 0

        for w in workflows:
            current = (w.current_stage or 'FUNDS').strip().upper()
            current_idx = STAGE_ORDER.get(current, -1)
            if current_idx <= 0:
                # Still at FUNDS or unknown; no previous stages to close
                continue

            # 1) Close workflow_sla alerts for any stage before current
            from alert_service import AlertService
            from alert_system_models import Alert

            for stage, idx in STAGE_ORDER.items():
                if stage == 'COMPLETED':
                    continue
                if idx >= current_idx:
                    break
                n = 0
                if not dry_run:
                    n = AlertService.close_previous_stage_alerts(w.id, stage, user_id=None)
                else:
                    alerts = Alert.query.filter(
                        Alert.workflow_id == w.id,
                        Alert.alert_type == 'workflow_sla',
                        Alert.alert_subtype == f'{stage}_delay',
                        Alert.status.in_(['active', 'acknowledged', 'snoozed'])
                    ).all()
                    n = len(alerts)
                if n:
                    total_alerts += n
                    print(f"  Workflow {w.id} (current={current}): closed {n} alert(s) for stage {stage}")

            # 2) Close obsolete OpsTasks and resolve obsolete RECOMMENDATION_EXECUTION issues
            if not dry_run:
                from services.task_auto_close_service import close_ops_tasks_and_resolve_issues_for_workflow
                tasks_closed, issues_resolved = close_ops_tasks_and_resolve_issues_for_workflow(w.id, None)
                total_tasks += tasks_closed
                total_issues += issues_resolved
                if tasks_closed or issues_resolved:
                    print(f"  Workflow {w.id} (current={current}): closed {tasks_closed} task(s), resolved {issues_resolved} issue(s)")

        print("")
        print(f"Done. Alerts closed: {total_alerts}, Tasks closed: {total_tasks}, Issues resolved: {total_issues}")
        if dry_run:
            print("(DRY RUN - no changes committed; re-run without --dry-run to apply task/issue closure)")
        return total_alerts, total_tasks, total_issues


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Backfill: close stale workflow alerts and tasks')
    parser.add_argument('--dry-run', action='store_true', help='Report only, do not commit')
    args = parser.parse_args()
    backfill(dry_run=args.dry_run)
