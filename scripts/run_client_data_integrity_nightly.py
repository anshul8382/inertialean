#!/usr/bin/env python3
"""
Local / pre-prod smoke for Client Data Integrity nightly snapshot.

Default: deterministic playbooks only (no Ollama). Guided chat keeps live LLM.

Examples:
  python3 scripts/run_client_data_integrity_nightly.py --print-summary
  python3 scripts/run_client_data_integrity_nightly.py --client-id 26 --print-client 26
  python3 scripts/run_client_data_integrity_nightly.py --llm   # opt-in overnight LLM (slow)
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
    ap.add_argument("--client-id", type=int, action="append", dest="client_ids")
    ap.add_argument("--include-inactive", action="store_true")
    ap.add_argument(
        "--llm",
        action="store_true",
        help="Opt-in: call local Ollama for attention text (slow; not used by Airflow DAG)",
    )
    ap.add_argument(
        "--no-llm",
        action="store_true",
        help="Deprecated alias: playbooks are already the default",
    )
    ap.add_argument(
        "--skip-sheet-verify",
        action="store_true",
        help="Skip Google Sheets price suppress step",
    )
    ap.add_argument("--sheet-max-lookups", type=int, default=250)
    ap.add_argument("--print-summary", action="store_true")
    ap.add_argument("--print-client", type=int, metavar="CLIENT_ID")
    args = ap.parse_args()

    from main import create_app
    from services.client_data_integrity_case_service import get_snapshot_client
    from services.client_data_integrity_nightly_service import run_client_data_integrity_nightly

    use_llm = bool(args.llm) and not bool(args.no_llm)

    app = create_app()
    with app.app_context():
        summary = run_client_data_integrity_nightly(
            active_only=not args.include_inactive,
            client_ids=args.client_ids,
            use_llm=use_llm,
            merge=bool(args.client_ids),
            verify_prices_from_sheets=not args.skip_sheet_verify,
            sheet_max_lookups=args.sheet_max_lookups,
        )
        if args.print_summary or not args.print_client:
            print(json.dumps(summary, indent=2, default=str))
        if args.print_client:
            row = get_snapshot_client(args.print_client)
            print(json.dumps(row, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
