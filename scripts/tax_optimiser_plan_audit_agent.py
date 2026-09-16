#!/usr/bin/env python3
"""
Run the Tax Optimiser Strategies v2 plan auditor (reads plan markdown + repo checks).

  python scripts/tax_optimiser_plan_audit_agent.py
  python scripts/tax_optimiser_plan_audit_agent.py --no-db
  python scripts/tax_optimiser_plan_audit_agent.py --no-smoke
  python scripts/tax_optimiser_plan_audit_agent.py --json
  python scripts/tax_optimiser_plan_audit_agent.py --markdown /tmp/txo_plan_audit.md

Env:
  TAX_OPTIMISER_PLAN_PATH  Override path to the .plan.md file

Exit code: 0 if no findings with status "fail", else 1.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main() -> int:
    p = argparse.ArgumentParser(description="Audit tax optimiser implementation vs Cursor plan")
    p.add_argument("--plan", type=Path, default=None, help="Path to tax_optimiser_strategies_v2 *.plan.md")
    p.add_argument("--no-db", action="store_true", help="Skip Flask/DB checks")
    p.add_argument("--no-smoke", action="store_true", help="Skip smoke_tax_optimiser_workflow.py")
    p.add_argument("--json", action="store_true", help="Print JSON only")
    p.add_argument("--markdown", type=Path, default=None, help="Write markdown report to file")
    args = p.parse_args()

    from agents.tax_optimiser_plan_auditor import (
        report_to_json,
        report_to_markdown,
        run_audit,
    )

    report = run_audit(
        plan_path=args.plan,
        with_db=not args.no_db,
        run_smoke=not args.no_smoke,
    )

    if args.json:
        print(report_to_json(report))
    else:
        md = report_to_markdown(report)
        print(md)
        if args.markdown:
            args.markdown.write_text(md, encoding="utf-8")
            print(f"\n(Wrote {args.markdown})")

    fails = sum(1 for f in report.findings if f.status == "fail")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
