#!/usr/bin/env python3
"""
Verify price findings vs Google Sheets → firm suppress JSON.

Default: one paginated *cycle* (cursor advances). Re-run overnight with gaps.

  python3 scripts/run_price_sheet_suppress.py --cycle
  python3 scripts/run_price_sheet_suppress.py --cycle --batch-lookups 80
  python3 scripts/run_price_sheet_suppress.py --one-shot --max-lookups 40 --kinds MISSING
  python3 scripts/run_price_sheet_suppress.py --rebuild-leftovers
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycle", action="store_true", help="Paginated cycle (default if no --one-shot)")
    ap.add_argument("--one-shot", action="store_true", help="Legacy single batch without cursor")
    ap.add_argument("--batch-lookups", type=int, default=80)
    ap.add_argument("--max-lookups", type=int, default=80)
    ap.add_argument("--tolerance-pct", type=float, default=0.5)
    ap.add_argument("--kinds", nargs="+", default=["SPIKE", "ZERO", "MISSING"])
    ap.add_argument("--no-spike-db-ack", action="store_true")
    ap.add_argument("--rebuild-leftovers", action="store_true")
    ap.add_argument("--reset-cursor", action="store_true")
    args = ap.parse_args()

    from main import create_app
    from services import price_accuracy_sheet_suppress_service as svc

    app = create_app()
    with app.app_context():
        if args.reset_cursor:
            svc.save_cursor({"run_id": None, "last_finding_id": 0, "cycles": 0, "done": False})
            print(json.dumps({"reset_cursor": True}))
            if not args.cycle and not args.one_shot and not args.rebuild_leftovers:
                return 0
        if args.rebuild_leftovers and not args.cycle and not args.one_shot:
            print(json.dumps(svc.rebuild_symbol_leftovers_queue(), indent=2, default=str))
            return 0
        if args.one_shot:
            summary = svc.verify_and_suppress_from_sheets(
                kinds=args.kinds,
                rel_tol=float(args.tolerance_pct) / 100.0,
                max_unique_lookups=int(args.max_lookups),
                also_ack_spikes_in_db=not args.no_spike_db_ack,
            )
        else:
            summary = svc.run_sheet_suppress_cycle(
                batch_lookups=int(args.batch_lookups or args.max_lookups),
                rel_tol=float(args.tolerance_pct) / 100.0,
                kinds=args.kinds,
            )
        print(json.dumps(summary, indent=2, default=str))
        return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
