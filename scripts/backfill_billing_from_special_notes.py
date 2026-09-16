#!/usr/bin/env python3
"""
Backfill empty agreement billing fields from special_note (fill-only, no overwrites).

Also fixes generated_pdf_path when a PDF exists on disk but the stored path is wrong.

Usage:

    python3 scripts/backfill_billing_from_special_notes.py --dry-run
    python3 scripts/backfill_billing_from_special_notes.py --apply
    python3 scripts/backfill_billing_from_special_notes.py --apply --client-id 9
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import create_app
from extensions import db
from models import Agreement, Client, Lead
from services.agreement_billing_backfill_service import (
    backfill_agreement_from_note,
    backfill_all_clients,
    fix_all_agreement_pdf_links,
)
from sqlalchemy.orm import joinedload
from services.agreement_overview_service import AgreementOverviewService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill empty billing fields from agreement special_note"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default is dry-run)",
    )
    parser.add_argument("--client-id", type=int, help="Limit to one client id")
    parser.add_argument("--json", metavar="PATH", help="Write full result JSON to file")
    parser.add_argument(
        "--pdf-only",
        action="store_true",
        help="Only fix agreement PDF web paths (no billing field backfill)",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    app = create_app()

    with app.app_context():
        if args.pdf_only:
            summary = fix_all_agreement_pdf_links(dry_run=dry_run)
            for row in summary.get("details", []):
                print(f"Agreement #{row['agreement_id']}: {row['change']}")
        elif args.client_id:
            client = Client.query.get(args.client_id)
            if not client:
                print(f"Client {args.client_id} not found")
                sys.exit(1)
            lead = Lead.query.filter_by(client_id=client.id).first()
            if not lead:
                print(f"No lead for client {args.client_id}")
                sys.exit(1)
            agreements = Agreement.query.options(
                joinedload(Agreement.variables),
                joinedload(Agreement.billing_rates),
                joinedload(Agreement.billing_schedules),
                joinedload(Agreement.lead),
            ).filter_by(lead_id=lead.id).all()
            if not agreements:
                print("No agreements")
                sys.exit(1)
            preferred = [
                a
                for a in agreements
                if (a.status or "").strip().lower()
                in AgreementOverviewService.ACTIVE_AGREEMENT_STATUSES
            ]
            agreement = AgreementOverviewService._pick_primary_agreement(preferred or agreements)
            result = backfill_agreement_from_note(agreement, dry_run=dry_run)
            if not dry_run:
                db.session.commit()
            summary = {"dry_run": dry_run, "details": [result]}
        else:
            summary = backfill_all_clients(dry_run=dry_run)

    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"Agreement billing backfill ({mode})")
    print(f"  agreements with changes: {summary.get('agreements_updated', len(summary.get('details', [])))}")
    print(f"  pdf links fixed:         {summary.get('pdf_links_fixed', 0)}")
    print()
    for row in summary.get("details", [])[:40]:
        name = row.get("client_name") or row.get("client_id") or ""
        print(f"Client {row.get('client_id')} {name} | Agreement #{row.get('agreement_id')}")
        for a in row.get("actions") or []:
            print(f"  + {a}")
        for s in row.get("skipped") or []:
            print(f"  - skip: {s}")
    rest = len(summary.get("details", [])) - 40
    if rest > 0:
        print(f"  ... and {rest} more (use --json)")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"\nWrote {args.json}")

    if dry_run:
        print("\nRe-run with --apply to persist.")


if __name__ == "__main__":
    main()
