#!/usr/bin/env python
"""
Dry-run: simulate consolidated orchestrator alerts + OpsTask eligibility (no DB writes).

Reports how many new alerts/tasks the current pipeline would create vs skip, and maps
to existing Alert / OpsTask rows so you can tune rules without deleting data.

Usage:
  python scripts/dry_run_alert_task_pipeline.py
  python scripts/dry_run_alert_task_pipeline.py --verbose
  python scripts/dry_run_alert_task_pipeline.py --client-id 42 --verbose
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, date, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _effective_assignee_for_issue(issue, get_assignee_for_issue_fn):
    if getattr(issue, "assigned_to", None):
        return issue.assigned_to
    return get_assignee_for_issue_fn(issue)


def _review_would_create_task(wf, get_assignee_for_review_status_fn):
    """Mirror create_ops_task_from_review_workflow eligibility (read-only)."""
    from models import OpsTask
    from services.task_assignment_service import MAX_DEADLINE_DAYS

    if not wf or wf.status == "closed":
        return False, "closed_or_missing"
    existing = OpsTask.query.filter_by(review_workflow_id=wf.id).filter(
        OpsTask.status.in_(["pending", "in_progress", "snoozed"])
    ).first()
    if existing:
        return False, f"has_open_task:{existing.id}"
    if wf.status not in ("initiated", "sent", "meeting"):
        return False, f"status_{wf.status}"
    rd = wf.review_date
    if isinstance(rd, date):
        cutoff = (datetime.utcnow() + timedelta(days=MAX_DEADLINE_DAYS)).date()
        if rd > cutoff:
            return False, "review_too_far_out"
    aid = wf.assigned_to or get_assignee_for_review_status_fn(wf)
    if not aid:
        return False, "no_assignee"
    return True, "would_create"


def main():
    parser = argparse.ArgumentParser(
        description="Dry-run alert orchestration + task backfill (read-only)"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Per-client / sample lines")
    parser.add_argument("--client-id", type=int, default=None, help="Only this client_id")
    parser.add_argument(
        "--sample",
        type=int,
        default=15,
        help="Max sample rows per category in verbose mode (default 15)",
    )
    args = parser.parse_args()

    from main import create_app
    from extensions import db
    from models import DataIntegrityIssue, OpsTask, ReviewWorkflow
    from agents.orchestrator import AlertOrchestrator, ALERT_AVAILABLE
    from services.task_assignment_service import (
        get_assignee_for_issue,
        get_assignee_for_review_status,
        get_assignee_for_alert,
    )

    if not ALERT_AVAILABLE:
        print("Alert models not available; orchestrator section will be skipped.")
        return 1

    from alert_system_models import Alert

    app = create_app()
    with app.app_context():
        # --- Inventory (existing) ---
        open_issue_statuses = ("open", "baseline")
        active_alert_statuses = ("active", "acknowledged", "snoozed")

        q_issues = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.status.in_(open_issue_statuses)
        )
        if args.client_id:
            q_issues = q_issues.filter(DataIntegrityIssue.client_id == args.client_id)

        issue_client_ids = (
            db.session.query(DataIntegrityIssue.client_id)
            .filter(DataIntegrityIssue.status.in_(open_issue_statuses))
            .distinct()
        )
        if args.client_id:
            issue_client_ids = issue_client_ids.filter(
                DataIntegrityIssue.client_id == args.client_id
            )
        client_ids = [r[0] for r in issue_client_ids.all()]

        alerts_by_type_status = defaultdict(int)
        for row in (
            db.session.query(Alert.alert_type, Alert.status, db.func.count(Alert.id))
            .group_by(Alert.alert_type, Alert.status)
            .all()
        ):
            alerts_by_type_status[f"{row[0]}/{row[1]}"] = row[2]

        ops_open = OpsTask.query.filter(
            OpsTask.status.in_(["pending", "in_progress", "snoozed"])
        )
        n_ops_issue_linked = ops_open.filter(OpsTask.data_integrity_issue_id.isnot(None)).count()
        n_ops_review_linked = ops_open.filter(OpsTask.review_workflow_id.isnot(None)).count()
        n_ops_manual = ops_open.filter(
            OpsTask.data_integrity_issue_id.is_(None),
            OpsTask.review_workflow_id.is_(None),
        ).count()

        issues_with_alert_id = q_issues.filter(DataIntegrityIssue.alert_id.isnot(None)).count()
        issues_without_alert_id = q_issues.filter(DataIntegrityIssue.alert_id.is_(None)).count()
        total_open_issues = q_issues.count()

        print("=== Existing inventory (read-only) ===")
        print(f"Clients with open/baseline issues: {len(client_ids)}")
        print(f"Open/baseline DataIntegrityIssues: {total_open_issues}")
        print(f"  linked to an alert_id: {issues_with_alert_id}")
        print(f"  no alert_id: {issues_without_alert_id}")
        print(f"Open OpsTasks (pending/in_progress/snoozed): issue-linked={n_ops_issue_linked}, "
              f"review-linked={n_ops_review_linked}, manual/other={n_ops_manual}")
        print("Alert counts by type/status:")
        for k in sorted(alerts_by_type_status.keys()):
            print(f"  {k}: {alerts_by_type_status[k]}")
        print()

        # --- Orchestrator simulation (consolidated agent_orchestrated) ---
        orch = AlertOrchestrator()
        now = datetime.utcnow()
        recent_cutoff = now - timedelta(days=7)

        would_create_alert = 0
        skip_no_alert = 0
        skip_existing_recent = 0
        samples_create = []
        samples_skip_alert = []
        samples_skip_existing = []

        for cid in sorted(client_ids):
            issues = (
                DataIntegrityIssue.query.filter(
                    DataIntegrityIssue.client_id == cid,
                    DataIntegrityIssue.status.in_(open_issue_statuses),
                )
                .order_by(DataIntegrityIssue.id)
                .all()
            )
            if not issues:
                continue
            context = orch.collect_client_context(cid)
            decision = orch.analyze_context(context, issues)
            existing_recent = Alert.query.filter(
                Alert.client_id == cid,
                Alert.status == "active",
                Alert.created_at >= recent_cutoff,
            ).first()
            # Any active consolidated-style alert in window (orchestrator only checks active + 7d)
            existing_agent = None
            if existing_recent:
                existing_agent = existing_recent

            if not decision["should_alert"]:
                skip_no_alert += 1
                if args.verbose and len(samples_skip_alert) < args.sample:
                    samples_skip_alert.append(
                        (cid, len(issues), decision.get("confidence"), decision.get("reasoning", [])[:2])
                    )
                continue
            if existing_agent:
                skip_existing_recent += 1
                if args.verbose and len(samples_skip_existing) < args.sample:
                    samples_skip_existing.append(
                        (
                            cid,
                            existing_agent.id,
                            existing_agent.alert_type,
                            existing_agent.alert_subtype,
                            existing_agent.created_at.isoformat() if existing_agent.created_at else "",
                            len(issues),
                        )
                    )
                continue
            would_create_alert += 1
            preview_uid = None
            try:
                preview_uid = get_assignee_for_alert(
                    "agent_orchestrated",
                    "consolidated",
                    workflow=None,
                    issues=issues,
                    client_id=cid,
                )
            except Exception:
                pass
            if args.verbose and len(samples_create) < args.sample:
                iids = [i.id for i in issues[:8]]
                samples_create.append((cid, len(issues), preview_uid, iids))

        print("=== Simulated: AlertOrchestrator consolidated alert (per client with issues) ===")
        print(f"Would create NEW consolidated alert: {would_create_alert}")
        print(f"Skip — orchestrator says no alert: {skip_no_alert}")
        print(f"Skip — active alert in last 7 days (same check as production): {skip_existing_recent}")
        if args.verbose:
            print("\nSample would-create (client_id, issue_count, preview_assignee_user_id, issue_ids[...]):")
            for row in samples_create:
                print(f"  {row}")
            print("\nSample skip no-alert (client_id, issue_count, confidence, reasoning[:2]):")
            for row in samples_skip_alert:
                print(f"  {row}")
            print("\nSample skip existing (client_id, alert_id, type, subtype, created_at, open_issues):")
            for row in samples_skip_existing:
                print(f"  {row}")
        print()

        # --- OpsTask simulation (issue-linked, same rules as backfill / _create_issue) ---
        has_open_task = 0
        would_new_task = 0
        gap_no_assignee = 0
        samples_task_ok = []
        samples_task_new = []
        samples_gap = []

        for issue in q_issues.order_by(DataIntegrityIssue.client_id, DataIntegrityIssue.id).all():
            existing = OpsTask.query.filter_by(data_integrity_issue_id=issue.id).filter(
                OpsTask.status.in_(["pending", "in_progress", "snoozed"])
            ).first()
            eff = _effective_assignee_for_issue(issue, get_assignee_for_issue)
            if existing:
                has_open_task += 1
                if args.verbose and len(samples_task_ok) < args.sample:
                    samples_task_ok.append(
                        (issue.id, issue.client_id, existing.id, issue.assigned_to, eff)
                    )
            elif eff:
                would_new_task += 1
                if args.verbose and len(samples_task_new) < args.sample:
                    samples_task_new.append((issue.id, issue.client_id, eff, issue.check_category))
            else:
                gap_no_assignee += 1
                if args.verbose and len(samples_gap) < args.sample:
                    samples_gap.append(
                        (issue.id, issue.client_id, issue.check_category, issue.check_name)
                    )

        print("=== Simulated: OpsTask from DataIntegrityIssue (open issues) ===")
        print(f"Already have open OpsTask for issue: {has_open_task}")
        print(f"Would create new OpsTask (assignee resolvable): {would_new_task}")
        print(f"Gap — no assignee from rules + issue.assigned_to (task not auto-created): {gap_no_assignee}")
        if args.verbose:
            print("\nSample has task (issue_id, client_id, ops_task_id, issue.assigned_to, effective_assignee):")
            for row in samples_task_ok:
                print(f"  {row}")
            print("\nSample would new task (issue_id, client_id, assignee_id, category):")
            for row in samples_task_new:
                print(f"  {row}")
            print("\nSample gap (issue_id, client_id, check_category, check_name):")
            for row in samples_gap:
                print(f"  {row}")
        print()

        # --- Review workflow tasks ---
        rw_q = ReviewWorkflow.query.filter(ReviewWorkflow.status != "closed")
        if args.client_id:
            rw_q = rw_q.filter(ReviewWorkflow.client_id == args.client_id)
        review_would = 0
        review_skip = 0
        review_samples = []
        for wf in rw_q.all():
            ok, reason = _review_would_create_task(wf, get_assignee_for_review_status)
            if ok:
                review_would += 1
                if args.verbose and len(review_samples) < args.sample:
                    review_samples.append((wf.id, wf.client_id, wf.status, wf.assigned_to))
            else:
                review_skip += 1

        print("=== Simulated: OpsTask from ReviewWorkflow (not closed) ===")
        print(f"Would create new review OpsTask: {review_would}")
        print(f"Would not create (has task, date, assignee, etc.): {review_skip}")
        if args.verbose and review_samples:
            print("Sample would-create (review_workflow_id, client_id, status, assigned_to):")
            for row in review_samples:
                print(f"  {row}")
        print()

        print("=== Reducing noise without missing issues ===")
        print("- Alerts: orchestrator already consolidates to one alert per client when it fires; "
              "skips if an active alert exists in the last 7 days.")
        print("- Tasks: one OpsTask per open issue when an assignee exists; fix gap_no_assignee by "
              "adding TaskAssignmentRule rows for those check_category values.")
        print("- Close stale workflow-stage alerts/tasks with: "
              "python scripts/backfill_workflow_stage_alerts_and_tasks.py --dry-run")
        return 0


if __name__ == "__main__":
    sys.exit(main())
