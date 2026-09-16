#!/usr/bin/env python3
"""
One-time cleanup: fix recommendation rows where action=HOLD but amount/qty imply BUY/SELL.

Safe workflow:
  1. --dry-run     Scan and print changes (default; no DB writes).
  2. --execute     Apply all changes in ONE transaction; on any error, full rollback.
                   Writes a JSON backup of old action/qty for manual --rollback.
  3. --rollback    Restore rows from a backup file created by --execute.

Examples:
  cd /home/inertia/app
  python scripts/repair_recommendation_actions.py --dry-run
  python scripts/repair_recommendation_actions.py --execute
  python scripts/repair_recommendation_actions.py --rollback backups/recommendation_action_repair_20260525_143022.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import Recommendation  # noqa: E402
from services.recommendation_trade_normalizer import (  # noqa: E402
    preview_recommendation_row_repair,
    repair_recommendation_row,
)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Not JSON serializable: {type(obj)!r}")


def _backup_dir() -> str:
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups")
    os.makedirs(base, exist_ok=True)
    return base


def collect_planned_changes(client_id: int | None = None) -> List[Dict[str, Any]]:
    q = Recommendation.query.filter(Recommendation.client_id.isnot(None))
    if client_id is not None:
        q = q.filter(Recommendation.client_id == client_id)
    rows = q.order_by(Recommendation.id).all()
    planned: List[Dict[str, Any]] = []
    for row in rows:
        would_change, before, after = preview_recommendation_row_repair(row)
        if not would_change:
            continue
        planned.append(
            {
                "id": row.id,
                "client_id": row.client_id,
                "security_id": row.security_id,
                "session_id": row.session_id,
                "before": before,
                "after": after,
            }
        )
    return planned


def run_dry_run(client_id: int | None) -> int:
    planned = collect_planned_changes(client_id)
    if not planned:
        print("No rows would change.")
        return 0
    print(f"Would update {len(planned)} recommendation(s):\n")
    for p in planned:
        print(
            f"  id={p['id']} client={p['client_id']} "
            f"{p['before']['action']} qty={p['before']['quantity']} -> "
            f"{p['after']['action']} qty={p['after']['quantity']}"
        )
    return len(planned)


def run_execute(client_id: int | None) -> str:
    planned = collect_planned_changes(client_id)
    if not planned:
        print("No rows to update.")
        return ""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(_backup_dir(), f"recommendation_action_repair_{stamp}.json")
    backup_doc = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "description": "Pre-repair snapshot for rollback (action + quantity only)",
        "row_count": len(planned),
        "rows": [
            {
                "id": p["id"],
                "client_id": p["client_id"],
                "action": p["before"]["action"],
                "quantity": p["before"]["quantity"],
            }
            for p in planned
        ],
    }

    try:
        for p in planned:
            row = db.session.get(Recommendation, p["id"])
            if not row:
                raise RuntimeError(f"Recommendation {p['id']} disappeared before update")
            if not repair_recommendation_row(row):
                raise RuntimeError(
                    f"Recommendation {p['id']} did not update (expected "
                    f"{p['before']} -> {p['after']})"
                )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"FAILED — all changes rolled back. Error: {exc}")
        raise

    with open(backup_path, "w", encoding="utf-8") as fh:
        json.dump(backup_doc, fh, indent=2)
        fh.write("\n")

    print(f"SUCCESS — updated {len(planned)} row(s).")
    print(f"Backup for rollback: {backup_path}")
    return backup_path


def run_rollback(backup_path: str, dry_run: bool) -> int:
    if not os.path.isfile(backup_path):
        print(f"Backup file not found: {backup_path}")
        return 1

    with open(backup_path, encoding="utf-8") as fh:
        backup_doc = json.load(fh)

    rows = backup_doc.get("rows") or []
    if not rows:
        print("Backup file has no rows.")
        return 0

    if dry_run:
        print(f"Would restore {len(rows)} row(s) from {backup_path}:\n")
        for item in rows:
            row = db.session.get(Recommendation, item["id"])
            if not row:
                print(f"  id={item['id']} MISSING in DB")
                continue
            cur_action = (row.action or "").strip().upper()
            cur_qty = int(float(row.quantity or 0))
            print(
                f"  id={item['id']} {cur_action} qty={cur_qty} -> "
                f"{item['action']} qty={item['quantity']}"
            )
        return len(rows)

    try:
        restored = 0
        for item in rows:
            row = db.session.get(Recommendation, item["id"])
            if not row:
                raise RuntimeError(f"Recommendation {item['id']} not found for rollback")
            row.action = item["action"]
            row.quantity = item["quantity"]
            restored += 1
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"ROLLBACK FAILED — no changes applied. Error: {exc}")
        raise

    print(f"Restored {restored} row(s) from {backup_path}")
    return restored


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Repair misrecorded recommendation actions.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned changes only (default if neither --execute nor --rollback).",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Apply repairs in one transaction; write rollback backup on success.",
    )
    mode.add_argument(
        "--rollback",
        metavar="BACKUP_JSON",
        help="Restore action/quantity from a backup file produced by --execute.",
    )
    p.add_argument(
        "--client-id",
        type=int,
        default=None,
        help="Limit to one client (optional).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    app = create_app()
    with app.app_context():
        if args.rollback:
            return 0 if run_rollback(args.rollback, dry_run=False) >= 0 else 1
        if args.execute:
            run_execute(args.client_id)
            return 0
        run_dry_run(args.client_id)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
