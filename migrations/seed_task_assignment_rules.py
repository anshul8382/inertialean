#!/usr/bin/env python3
"""
Seed default TaskAssignmentRule records.
Run after add_task_assignment_rule.py migration.

Policy (resolved at seed time to user ids via roles):
- workflow_stage FUNDS/RECOS/NOTIFY/EXEC/UPDATE → ops_manager role
- issue_category PORTFOLIO_PERFORMANCE → admin role (fallback only;
  runtime prefers Client.advisor_id for integrity issues)
- other listed issue categories → ops_manager role (fallback only when client has no advisor)
- review_status sent → admin role; other review statuses → ops_manager role

Runtime note: get_assignee_for_issue() assigns open integrity issues to the
client's advisor when Client.advisor_id is set; category rules are fallback."""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from models import TaskAssignmentRule


def seed():
    app = create_app()
    with app.app_context():
        from services.task_role_assignee import resolve_user_id_for_assignment_role

        admin_id = resolve_user_id_for_assignment_role("admin")
        ops_id = resolve_user_id_for_assignment_role("ops_manager")

        if not admin_id:
            print("WARNING: No user resolved for role 'admin'. Skipping rules that need admin.")
        if not ops_id:
            print("WARNING: No user resolved for role 'ops_manager'. Skipping rules that need ops_manager.")

        def uid_for(role_key: str):
            if role_key == "admin":
                return admin_id
            if role_key == "ops_manager":
                return ops_id
            return None

        rules_data = [
            ("workflow_stage", "FUNDS", "ops_manager"),
            ("workflow_stage", "RECOS", "ops_manager"),
            ("workflow_stage", "NOTIFY", "ops_manager"),
            ("workflow_stage", "EXEC", "ops_manager"),
            ("workflow_stage", "UPDATE", "ops_manager"),
            ("issue_category", "PORTFOLIO_PERFORMANCE", "admin"),
            ("issue_category", "RECOMMENDATION_MATCH", "ops_manager"),
            ("issue_category", "CASHFLOW", "ops_manager"),
            ("issue_category", "HOLDINGS", "ops_manager"),
            ("issue_category", "DUPLICATES", "ops_manager"),
            ("issue_category", "DATE_INTEGRITY", "ops_manager"),
            ("issue_category", "NEGATIVE_VALUES", "ops_manager"),
            ("issue_category", "CORPORATE_ACTION", "ops_manager"),
            ("issue_category", "RECOMMENDATION_EXECUTION", "ops_manager"),
            ("review_status", "initiated", "ops_manager"),
            ("review_status", "sent", "admin"),
            ("review_status", "meeting", "ops_manager"),
            ("review_status", "closed", "ops_manager"),
        ]

        added = 0
        for rule_type, match_value, assignee_role in rules_data:
            user_id = uid_for(assignee_role)
            if not user_id:
                continue

            existing = TaskAssignmentRule.query.filter_by(
                rule_type=rule_type,
                match_value=match_value,
            ).first()
            if existing:
                continue

            rule = TaskAssignmentRule(
                rule_type=rule_type,
                match_value=match_value,
                assigned_user_id=user_id,
                priority=0,
                is_active=True,
            )
            db.session.add(rule)
            added += 1
            print(f"  Added: {rule_type}={match_value} -> role={assignee_role} user_id={user_id}")

        db.session.commit()
        print(f"Seed complete: {added} rules added.")


if __name__ == "__main__":
    seed()
