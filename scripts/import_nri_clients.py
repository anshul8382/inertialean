#!/usr/bin/env python3
"""Set client.is_nri=True from data/regulatory/nri_clients.json.

JSON format: [{"id": 12, "name": "Ajit R"}, ...]
Match by id; warn when name differs.

Usage:
  python3 scripts/import_nri_clients.py
  python3 scripts/import_nri_clients.py --dry-run
  python3 scripts/import_nri_clients.py --clear-others
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DEFAULT_PATH = os.path.join(ROOT, "data", "regulatory", "nri_clients.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import NRI client flags from JSON")
    parser.add_argument("--file", default=DEFAULT_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--clear-others",
        action="store_true",
        help="Set is_nri=False for clients not listed in the JSON",
    )
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as f:
        raw = json.load(f)
    rows = [r for r in raw if isinstance(r, dict) and r.get("id") is not None]
    if not rows:
        print(f"No id rows in {args.file} — add {{\"id\": N, \"name\": \"...\"}} entries.")
        return 1

    from main import create_app
    from extensions import db
    from models import Client

    app = create_app()
    with app.app_context():
        ids = [int(r["id"]) for r in rows]
        updated = 0
        warnings = []
        for r in rows:
            cid = int(r["id"])
            expected_name = (r.get("name") or "").strip()
            client = db.session.get(Client, cid)
            if not client:
                warnings.append(f"missing client id={cid} name={expected_name!r}")
                continue
            if expected_name and client.name.strip().lower() != expected_name.lower():
                warnings.append(
                    f"name mismatch id={cid}: db={client.name!r} json={expected_name!r}"
                )
            if not args.dry_run:
                client.is_nri = True
            updated += 1
            print(f"{'DRY ' if args.dry_run else ''}NRI=1 id={cid} {client.name}")

        cleared = 0
        if args.clear_others:
            q = Client.query.filter(~Client.id.in_(ids)).filter(Client.is_nri.is_(True))
            for c in q.all():
                print(f"{'DRY ' if args.dry_run else ''}NRI=0 id={c.id} {c.name}")
                if not args.dry_run:
                    c.is_nri = False
                cleared += 1

        if not args.dry_run:
            db.session.commit()

        print(f"\nUpdated={updated} cleared={cleared} warnings={len(warnings)}")
        for w in warnings:
            print("WARN:", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
