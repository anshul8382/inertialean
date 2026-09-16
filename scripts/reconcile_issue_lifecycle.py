#!/usr/bin/env python
"""
Evaluate open issues and auto-resolve ones whose root cause is cleared.

Usage:
  python scripts/reconcile_issue_lifecycle.py --dry-run
  python scripts/reconcile_issue_lifecycle.py
  python scripts/reconcile_issue_lifecycle.py --category PORTFOLIO_PERFORMANCE --client-id 123
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _parse_categories(category_values):
    out = []
    for raw in category_values or []:
        parts = [p.strip() for p in str(raw).split(",")]
        out.extend([p for p in parts if p])
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Issue lifecycle healer (root-cause clear => resolve issue/task/alert)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Evaluate only; no writes")
    parser.add_argument("--client-id", type=int, help="Optional client scope")
    parser.add_argument(
        "--category",
        action="append",
        default=[],
        help="Issue category (repeat or comma-separated). Default: RECOMMENDATION_EXECUTION,PORTFOLIO_PERFORMANCE",
    )
    parser.add_argument("--limit", type=int, default=0, help="Optional max issues to evaluate")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args()

    from main import create_app
    from services.issue_healer_service import run_issue_healer

    app = create_app()
    with app.app_context():
        stats = run_issue_healer(
            dry_run=args.dry_run,
            client_id=args.client_id,
            categories=_parse_categories(args.category),
            limit=args.limit or None,
            actor_user_id=None,
        )
        if args.json:
            print(json.dumps(stats, default=str))
            return

        print("=== Issue lifecycle healer ===")
        print(f"  dry_run: {stats.get('dry_run')}")
        print(f"  categories: {stats.get('categories')}")
        print(f"  evaluated: {stats.get('evaluated')}")
        print(f"  cleared: {stats.get('cleared')}")
        print(f"  issues_resolved: {stats.get('issues_resolved')}")
        print(f"  tasks_closed: {stats.get('tasks_closed')}")
        print(f"  linked_alerts_resolved: {stats.get('linked_alerts_resolved')}")
        print(f"  client_stale_alerts_resolved: {stats.get('client_stale_alerts_resolved')}")
        if args.dry_run:
            print("\n(DRY RUN: no changes committed)")


if __name__ == "__main__":
    main()
