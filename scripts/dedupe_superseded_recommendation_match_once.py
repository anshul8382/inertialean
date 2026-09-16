#!/usr/bin/env python
"""
One-off: resolve RECOMMENDATION_MATCH line-item issues (and complete linked OpsTasks)
superseded by cycle-level non-execution, for all affected clients.

Uses the same logic as DataIntegrityManager + recommendation_issue_hierarchy_service.
Safe to re-run; only issues matching current supersede rules are touched.

Usage:
  python scripts/dedupe_superseded_recommendation_match_once.py
  python scripts/dedupe_superseded_recommendation_match_once.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import distinct

from extensions import db
from main import create_app
from models import DataIntegrityIssue
from services.recommendation_issue_hierarchy_service import (
    TRADE_LEVEL_CHECK_NAMES,
    get_workflow_ids_with_cycle_level_non_execution,
    resolve_trade_level_issues_superseded_by_cycle,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dedupe superseded recommendation match issues once.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only; roll back at the end (no DB changes).",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        rows = (
            db.session.query(distinct(DataIntegrityIssue.client_id))
            .filter(
                DataIntegrityIssue.check_category == "RECOMMENDATION_MATCH",
                DataIntegrityIssue.check_name.in_(TRADE_LEVEL_CHECK_NAMES),
                DataIntegrityIssue.status.in_(["open", "baseline"]),
                DataIntegrityIssue.recommendation_id.isnot(None),
            )
            .all()
        )
        client_ids = [r[0] for r in rows if r[0] is not None]

        print(f"Clients with candidate RECOMMENDATION_MATCH issues: {len(client_ids)}")

        total_resolved = 0
        errors = 0
        for cid in sorted(client_ids):
            try:
                blocked = get_workflow_ids_with_cycle_level_non_execution(cid)
                n = resolve_trade_level_issues_superseded_by_cycle(cid, blocked)
                if n:
                    print(f"  client_id={cid} blocked_workflows={sorted(blocked)} resolved_issues={n}")
                total_resolved += n
                if not args.dry_run:
                    db.session.commit()
                else:
                    db.session.rollback()
            except Exception as e:
                db.session.rollback()
                errors += 1
                print(f"  ERROR client_id={cid}: {e}")

        if args.dry_run:
            print(f"[dry-run] Would resolve {total_resolved} issue(s); no changes committed.")
        else:
            print(f"Done. Resolved {total_resolved} issue(s). errors={errors}")


if __name__ == "__main__":
    main()
