#!/usr/bin/env python
"""
One-time cleanup: unify legacy RECOMMENDATION_EXECUTION SLA issue names.

Goals:
- Normalize check_name to `workflow_stalled` for legacy SLA issues:
  funds_not_received, recos_not_sent, recos_not_executed.
- Merge duplicate open/baseline issues per (client_id, workflow_id) into one canonical issue.
- Preserve subtype context in details:
  - sla_breach_type (primary subtype)
  - sla_breach_types (all observed subtypes in group)
  - related_alerts (workflow_stalled + subtype names)
- Resolve duplicate issues and close their open OpsTasks.
- Ensure canonical issue has an open OpsTask (if assignable).

Usage:
  python scripts/cleanup_workflow_sla_issue_unification.py --dry-run
  python scripts/cleanup_workflow_sla_issue_unification.py
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LEGACY_NAMES = ("funds_not_received", "recos_not_sent", "recos_not_executed")
CANONICAL_NAME = "workflow_stalled"
OPEN_ISSUE_STATUSES = ("open", "baseline")
OPEN_TASK_STATUSES = ("pending", "in_progress", "snoozed")
STAGE_ORDER = {"FUNDS": 0, "RECOS": 1, "NOTIFY": 2, "EXEC": 3, "UPDATE": 4, "COMPLETED": 5}


def _extract_workflow_id(issue) -> int | None:
    details = issue.details or {}
    wid = details.get("workflow_id")
    try:
        return int(wid) if wid is not None else None
    except (TypeError, ValueError):
        return None


def _issue_rank(issue) -> Tuple[int, datetime]:
    """
    Prefer an existing canonical issue, then higher severity, then oldest detected.
    Lower tuple wins.
    """
    sev_rank = {"critical": 0, "warning": 1, "info": 2}
    canonical_bias = 0 if (issue.check_name or "").lower() == CANONICAL_NAME else 1
    sev = sev_rank.get((issue.severity or "info").lower(), 3)
    detected = issue.detected_at or datetime.utcnow()
    return (canonical_bias, sev, detected)


def _pick_primary_subtype(subtypes: List[str], current_stage: str | None) -> str:
    if not subtypes:
        return "workflow_stalled"
    stage_to_subtype = {
        "FUNDS": "funds_not_received",
        "RECOS": "recos_not_sent",
        "NOTIFY": "recos_not_sent",
        "EXEC": "recos_not_executed",
        "UPDATE": "recos_not_executed",
    }
    st = (current_stage or "").upper()
    stage_hint = stage_to_subtype.get(st)
    if stage_hint and stage_hint in subtypes:
        return stage_hint
    if "workflow_stalled" in subtypes:
        return "workflow_stalled"
    return subtypes[0]


def _merge_alert_tokens(existing: List[str], incoming: List[str]) -> List[str]:
    out = []
    for token in (existing or []) + (incoming or []):
        t = str(token).strip()
        if t and t not in out:
            out.append(t)
    if "workflow_stalled" not in out:
        out.insert(0, "workflow_stalled")
    return out


def run_cleanup(dry_run: bool = False, verbose: bool = False) -> Dict[str, int]:
    from main import create_app
    from extensions import db
    from models import DataIntegrityIssue, OpsTask
    from services.task_assignment_service import create_ops_task_from_issue

    app = create_app()
    stats = {
        "groups_processed": 0,
        "issues_canonicalized": 0,
        "issues_resolved_duplicates": 0,
        "tasks_closed_duplicates": 0,
        "tasks_created_canonical": 0,
    }

    with app.app_context():
        candidates = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.check_category == "RECOMMENDATION_EXECUTION",
            DataIntegrityIssue.status.in_(OPEN_ISSUE_STATUSES),
            DataIntegrityIssue.check_name.in_(list(LEGACY_NAMES) + [CANONICAL_NAME]),
        ).all()

        grouped = defaultdict(list)
        skipped_no_workflow = 0
        for issue in candidates:
            wid = _extract_workflow_id(issue)
            if wid is None:
                skipped_no_workflow += 1
                continue
            grouped[(issue.client_id, wid)].append(issue)

        now = datetime.utcnow()
        for (client_id, workflow_id), issues in grouped.items():
            if not issues:
                continue
            stats["groups_processed"] += 1

            # canonical = best existing issue by rank
            issues_sorted = sorted(issues, key=_issue_rank)
            canonical = issues_sorted[0]
            others = [i for i in issues_sorted[1:]]

            # Build merged subtype context from group
            subtypes = []
            related_alerts = []
            for i in issues:
                name = (i.check_name or "").lower()
                d = i.details or {}
                subtype = (d.get("sla_breach_type") or "").lower()
                if subtype:
                    subtypes.append(subtype)
                if name:
                    subtypes.append(name)
                related_alerts.extend(d.get("related_alerts") or [])
                if name in LEGACY_NAMES:
                    related_alerts.append(name)
                if name == CANONICAL_NAME:
                    related_alerts.append(CANONICAL_NAME)

            # Normalize subtype list ordering
            ordered_subtypes = []
            for x in subtypes:
                if x and x not in ordered_subtypes:
                    ordered_subtypes.append(x)
            primary_subtype = _pick_primary_subtype(
                ordered_subtypes,
                (canonical.details or {}).get("current_stage"),
            )

            # Update canonical issue
            canonical_changed = False
            c_details = dict(canonical.details or {})
            if (canonical.check_name or "").lower() != CANONICAL_NAME:
                canonical_changed = True
                if not dry_run:
                    canonical.check_name = CANONICAL_NAME

            merged_related = _merge_alert_tokens(c_details.get("related_alerts") or [], related_alerts)
            if c_details.get("related_alerts") != merged_related:
                canonical_changed = True
                c_details["related_alerts"] = merged_related

            if c_details.get("sla_breach_type") != primary_subtype:
                canonical_changed = True
                c_details["sla_breach_type"] = primary_subtype

            if c_details.get("sla_breach_types") != ordered_subtypes:
                canonical_changed = True
                c_details["sla_breach_types"] = ordered_subtypes

            if "workflow_id" not in c_details:
                canonical_changed = True
                c_details["workflow_id"] = workflow_id

            if canonical_changed:
                stats["issues_canonicalized"] += 1
                if not dry_run:
                    canonical.details = c_details
                    if "Related SLA alerts:" not in (canonical.message or ""):
                        related_str = ", ".join(merged_related)
                        canonical.message = (
                            f"{(canonical.message or '').strip()} "
                            f"Related SLA alerts: {related_str}."
                        ).strip()[:2000]

            # Carry alert_id forward if canonical missing one
            if not dry_run and not canonical.alert_id:
                for other in others:
                    if other.alert_id:
                        canonical.alert_id = other.alert_id
                        break

            # Resolve duplicates and close their tasks
            for dup in others:
                stats["issues_resolved_duplicates"] += 1
                if not dry_run:
                    dup.status = "resolved"
                    dup.resolution_type = "merged_duplicate"
                    dup.resolved_at = now
                    dup.resolution_notes = (
                        f"Merged into issue #{canonical.id} by workflow SLA cleanup script."
                    )

                open_tasks = OpsTask.query.filter(
                    OpsTask.data_integrity_issue_id == dup.id,
                    OpsTask.status.in_(OPEN_TASK_STATUSES),
                ).all()
                for t in open_tasks:
                    stats["tasks_closed_duplicates"] += 1
                    if not dry_run:
                        t.status = "completed"
                        t.completed_at = now
                        t.completed_by = None
                        t.reminder_at = None
                        t.reminder_sent = True
                        t.updated_at = now
                        prefix = f"[Merged duplicate issue #{dup.id} -> #{canonical.id}] "
                        t.notes = (prefix + (t.notes or ""))[:500]

            # Ensure canonical has an open task
            open_canonical_task = OpsTask.query.filter(
                OpsTask.data_integrity_issue_id == canonical.id,
                OpsTask.status.in_(OPEN_TASK_STATUSES),
            ).first()
            if not open_canonical_task and not dry_run:
                task = create_ops_task_from_issue(canonical, assignee_id=canonical.assigned_to)
                if task:
                    stats["tasks_created_canonical"] += 1

            if verbose:
                print(
                    f"client={client_id} workflow={workflow_id} "
                    f"canonical={canonical.id} dup_count={len(others)} "
                    f"subtypes={ordered_subtypes}"
                )

        if dry_run:
            print(f"(dry-run) skipped groups with no workflow_id: {skipped_no_workflow}")
        else:
            if skipped_no_workflow and verbose:
                print(f"skipped groups with no workflow_id: {skipped_no_workflow}")
            db.session.commit()

    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Cleanup and unify workflow SLA issue names into workflow_stalled."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only, no DB writes")
    parser.add_argument("--verbose", action="store_true", help="Print per-group details")
    args = parser.parse_args()
    result = run_cleanup(dry_run=args.dry_run, verbose=args.verbose)
    print("Cleanup complete:")
    for k, v in result.items():
        print(f"  {k}: {v}")
    if args.dry_run:
        print("(DRY RUN - no changes committed)")
