#!/usr/bin/env python3
"""
Align TaskAssignmentRule with role-based policy:
- workflow_stage FUNDS, RECOS, NOTIFY, EXEC, UPDATE → ops_manager role
- issue_category PORTFOLIO_PERFORMANCE → admin role

Upserts by (rule_type, match_value): updates assigned_user_id if rule exists.

Usage:
  python migrations/align_portfolio_workflow_task_assignment.py
  python migrations/align_portfolio_workflow_task_assignment.py --dry-run
  python migrations/align_portfolio_workflow_task_assignment.py --reapply-open-issues
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _reapply_open_issues(dry_run: bool) -> int:
    """Set assigned_to on open issues (and linked open OpsTasks) from current rules."""
    from extensions import db
    from models import DataIntegrityIssue, OpsTask
    from services.task_assignment_service import get_assignee_for_issue

    open_st = ("open", "baseline")
    task_st = ("pending", "in_progress", "snoozed")
    categories = ("RECOMMENDATION_EXECUTION", "PORTFOLIO_PERFORMANCE")
    n = 0
    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.status.in_(open_st),
        DataIntegrityIssue.check_category.in_(categories),
    ).all()
    for issue in issues:
        uid = get_assignee_for_issue(issue)
        if not uid or issue.assigned_to == uid:
            continue
        n += 1
        if dry_run:
            continue
        issue.assigned_to = uid
        for t in OpsTask.query.filter(
            OpsTask.data_integrity_issue_id == issue.id,
            OpsTask.status.in_(task_st),
        ).all():
            t.assigned_to = uid
    return n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--reapply-open-issues",
        action="store_true",
        help="Update assigned_to on open RECOMMENDATION_EXECUTION / PORTFOLIO_PERFORMANCE issues and their open OpsTasks",
    )
    args = parser.parse_args()

    from main import create_app
    from extensions import db
    from models import TaskAssignmentRule
    from services.task_assignment_service import invalidate_rules_cache
    from services.task_role_assignee import resolve_user_id_for_assignment_role

    app = create_app()
    with app.app_context():
        admin_id = resolve_user_id_for_assignment_role("admin")
        ops_id = resolve_user_id_for_assignment_role("ops_manager")

        if not admin_id:
            print("ERROR: No user resolved for role 'admin'.")
            return 1
        if not ops_id:
            print("ERROR: No user resolved for role 'ops_manager'.")
            return 1

        print(f"Resolved admin role -> user_id={admin_id}")
        print(f"Resolved ops_manager role -> user_id={ops_id}")

        workflow_stages = ("FUNDS", "RECOS", "NOTIFY", "EXEC", "UPDATE")
        updates = []
        for stage in workflow_stages:
            updates.append(("workflow_stage", stage, ops_id))
        updates.append(("issue_category", "PORTFOLIO_PERFORMANCE", admin_id))

        changed = 0
        for rule_type, match_value, user_id in updates:
            rule = TaskAssignmentRule.query.filter_by(
                rule_type=rule_type,
                match_value=match_value,
            ).first()
            if rule:
                if rule.assigned_user_id != user_id:
                    print(
                        f"  UPDATE {rule_type}={match_value}: "
                        f"user {rule.assigned_user_id} -> {user_id}"
                    )
                    changed += 1
                    if not args.dry_run:
                        rule.assigned_user_id = user_id
                        rule.is_active = True
            else:
                print(f"  INSERT {rule_type}={match_value} -> user {user_id}")
                changed += 1
                if not args.dry_run:
                    db.session.add(
                        TaskAssignmentRule(
                            rule_type=rule_type,
                            match_value=match_value,
                            assigned_user_id=user_id,
                            priority=0,
                            is_active=True,
                        )
                    )

        reassign_n = 0
        if args.reapply_open_issues:
            reassign_n = _reapply_open_issues(args.dry_run)
            print(
                f"\nOpen issues assignee updates: {reassign_n} issue(s) would change"
                if args.dry_run
                else f"\nOpen issues assignee updates: {reassign_n} issue(s) updated"
            )

        if args.dry_run:
            print(f"\nDRY RUN: would touch {changed} rule row(s); no commit.")
            return 0

        db.session.commit()
        invalidate_rules_cache()
        print(f"\nDone. Updated/inserted {changed} rule row(s). Cache invalidated.")
        if not args.reapply_open_issues:
            print("Tip: pass --reapply-open-issues to align existing open issues/tasks to new rules.")
        return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
