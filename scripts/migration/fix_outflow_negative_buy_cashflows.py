#!/usr/bin/env python3
"""
Fix mistagged auto BUY cashflows: type OUTFLOW + negative amount → INFLOW.

Scope (same population as the verification report):
  - type = OUTFLOW (case-insensitive)
  - amount < 0
  - description starts with "BUY " (auto trade lines)

Amount is never changed — only type is corrected.

Default is dry-run (no commit). After verifying
docs/cashflow_outflow_negative_mistags_YYYY-MM-DD.csv, run with --apply.

Usage (from project root):
  # Preview (safe)
  python3 scripts/migration/fix_outflow_negative_buy_cashflows.py

  # Write changes
  python3 scripts/migration/fix_outflow_negative_buy_cashflows.py --apply

  # Optional: only IDs listed in the verification CSV
  python3 scripts/migration/fix_outflow_negative_buy_cashflows.py \\
      --from-csv docs/cashflow_outflow_negative_mistags_2026-08-25.csv --apply
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _as_date_iso(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "date"):
        try:
            return value.date().isoformat()
        except Exception:
            pass
    try:
        return value.isoformat()[:10]
    except Exception:
        return str(value)[:10]


def _load_ids_from_csv(path: Path) -> Set[int]:
    ids: Set[int] = set()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = row.get("cashflow_id") or row.get("id")
            if not raw:
                continue
            ids.add(int(raw))
    return ids


def find_candidates(from_ids: Optional[Set[int]] = None) -> List[Any]:
    from models import Cashflow, Client
    from sqlalchemy import func
    from sqlalchemy.orm import joinedload

    q = (
        Cashflow.query.outerjoin(Client, Client.id == Cashflow.client_id)
        .filter(
            func.upper(Cashflow.type) == "OUTFLOW",
            Cashflow.amount < 0,
            Cashflow.description.like("BUY %"),
        )
        .order_by(Client.name, Cashflow.date, Cashflow.id)
    )
    if from_ids is not None:
        if not from_ids:
            return []
        q = q.filter(Cashflow.id.in_(from_ids))
    return q.all()


def run(*, apply: bool, from_csv: Optional[Path], log_dir: Path) -> int:
    from main import create_app
    from models import Client, db

    app = create_app()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = "APPLY" if apply else "DRY_RUN"
    log_path = log_dir / f"fix_outflow_negative_buy_{stamp}_{mode}.log"
    out_csv = log_dir / f"fix_outflow_negative_buy_{stamp}_{mode}.csv"

    from_ids: Optional[Set[int]] = None
    if from_csv is not None:
        if not from_csv.is_file():
            print(f"ERROR: CSV not found: {from_csv}", file=sys.stderr)
            return 2
        from_ids = _load_ids_from_csv(from_csv)
        print(f"Restricting to {len(from_ids)} cashflow_id(s) from {from_csv}")

    with app.app_context():
        rows = find_candidates(from_ids)
        changes: List[Dict[str, Any]] = []
        for cf in rows:
            client = Client.query.get(cf.client_id)
            changes.append(
                {
                    "cashflow_id": cf.id,
                    "client_id": cf.client_id,
                    "client_name": client.name if client else "",
                    "cashflow_date": _as_date_iso(cf.date),
                    "amount": f"{float(cf.amount):.2f}",
                    "old_type": cf.type,
                    "new_type": "INFLOW",
                    "description": (cf.description or "")[:200],
                }
            )
            if apply:
                cf.type = "INFLOW"

        if apply and changes:
            db.session.commit()
        elif not apply:
            db.session.rollback()

        fieldnames = [
            "cashflow_id",
            "client_id",
            "client_name",
            "cashflow_date",
            "amount",
            "old_type",
            "new_type",
            "description",
        ]
        with out_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for row in changes:
                w.writerow(row)

        lines = [
            "=" * 72,
            f"Fix OUTFLOW + negative BUY cashflows — {datetime.now().isoformat(sep=' ', timespec='seconds')}",
            f"Mode: {mode}",
            f"Candidates: {len(changes)}",
            f"CSV: {out_csv}",
            "=" * 72,
            "",
        ]
        if not changes:
            lines.append("No matching rows. Nothing to do.")
        else:
            by_client: Dict[str, int] = {}
            for row in changes:
                key = f"{row['client_id']} {row['client_name']}"
                by_client[key] = by_client.get(key, 0) + 1
            lines.append("By client:")
            for key, n in sorted(by_client.items(), key=lambda x: -x[1]):
                lines.append(f"  {n:3d}  {key}")
            lines.append("")
            if apply:
                lines.append(f"COMMITTED: updated type OUTFLOW → INFLOW on {len(changes)} row(s). Amounts unchanged.")
            else:
                lines.append("DRY RUN: no DB writes. Re-run with --apply after verification.")

        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        print(f"\nLog: {log_path}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fix OUTFLOW + negative amount BUY cashflows → INFLOW (amount unchanged)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit type changes. Without this flag, dry-run only.",
    )
    parser.add_argument(
        "--from-csv",
        type=Path,
        default=None,
        help="Only fix cashflow_id values listed in this verification CSV.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=APP_ROOT / "docs",
        help="Directory for log + result CSV (default: docs/).",
    )
    args = parser.parse_args()
    args.log_dir.mkdir(parents=True, exist_ok=True)
    return run(apply=args.apply, from_csv=args.from_csv, log_dir=args.log_dir)


if __name__ == "__main__":
    raise SystemExit(main())
