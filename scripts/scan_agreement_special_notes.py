#!/usr/bin/env python3
"""
Scan agreement special_note text and compare heuristic parses to structured billing fields.

Dry-run only — does not modify the database.

Usage (from repo root):

    python3 scripts/scan_agreement_special_notes.py
    python3 scripts/scan_agreement_special_notes.py --csv /tmp/special_notes_scan.csv
    python3 scripts/scan_agreement_special_notes.py --confidence high
    python3 scripts/scan_agreement_special_notes.py --missing-only
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import create_app
from models import Agreement, BillingRateStructure, Client, Lead
from services.agreement_overview_service import AgreementOverviewService
from services.special_note_parser import diff_against_current, parse_special_note
from sqlalchemy.orm import joinedload


def _get_special_note(agreement: Agreement) -> str:
    for v in agreement.variables or []:
        if (v.variable_name or "").strip().lower() == "special_note":
            if (v.variable_value or "").strip():
                return v.variable_value.strip()
    if agreement.agreement_data:
        try:
            data = json.loads(agreement.agreement_data)
            sn = (data.get("special_note") or "").strip()
            if sn:
                return sn
        except (json.JSONDecodeError, TypeError):
            pass
    return ""


def _current_billing_state(agreement: Agreement) -> dict:
    config = {}
    if agreement.agreement_data:
        try:
            config = json.loads(agreement.agreement_data) or {}
        except (json.JSONDecodeError, TypeError):
            config = {}

    vars_map = {
        (v.variable_name or "").strip(): v.variable_value
        for v in (agreement.variables or [])
    }

    advisory = config.get("advisory_model") or vars_map.get("advisory_model")
    rate_pcts = []
    for r in agreement.billing_rates or []:
        if not getattr(r, "is_active", True):
            continue
        if r.rate_percentage is not None:
            rate_pcts.append(round(float(r.rate_percentage) * 100, 4))

    return {
        "billing_frequency": vars_map.get("billing_frequency"),
        "period_start_month": vars_map.get("period_start_month"),
        "valuation_date_rule": vars_map.get("valuation_date_rule"),
        "advisory_model": advisory,
        "fixed_annual_fee": vars_map.get("fixed_annual_fee"),
        "rate_pcts": rate_pcts,
        "has_active_schedule": any(
            getattr(s, "is_active", False) for s in (agreement.billing_schedules or [])
        ),
        "has_active_rates": bool(rate_pcts),
    }


def _pick_agreement_for_client(client_id: int) -> Agreement | None:
    lead = Lead.query.filter_by(client_id=client_id).first()
    if not lead:
        return None
    agreements = (
        Agreement.query.options(
            joinedload(Agreement.variables),
            joinedload(Agreement.billing_rates),
            joinedload(Agreement.billing_schedules),
        )
        .filter_by(lead_id=lead.id)
        .all()
    )
    if not agreements:
        return None
    preferred = [
        a
        for a in agreements
        if (a.status or "").strip().lower()
        in AgreementOverviewService.ACTIVE_AGREEMENT_STATUSES
    ]
    return AgreementOverviewService._pick_primary_agreement(preferred or agreements)


def scan_all(
    confidence_filter: str | None = None,
    missing_only: bool = False,
) -> list[dict]:
    rows: list[dict] = []

    clients = Client.query.order_by(Client.name).all()
    for client in clients:
        agreement = _pick_agreement_for_client(client.id)
        if not agreement:
            if missing_only:
                continue
            rows.append(
                {
                    "client_id": client.id,
                    "client_name": client.name,
                    "agreement_id": "",
                    "agreement_status": "",
                    "special_note": "",
                    "confidence": "",
                    "would_fill": False,
                    "gaps": "no_agreement",
                    "conflicts": "",
                    "proposed_json": "",
                }
            )
            continue

        note = _get_special_note(agreement)
        if not note and missing_only:
            continue

        parsed = parse_special_note(note)
        if confidence_filter and parsed.confidence != confidence_filter:
            continue

        current = _current_billing_state(agreement)
        diff = diff_against_current(parsed, current)

        overview_flags = []
        if not current.get("billing_frequency"):
            overview_flags.append("missing_billing_frequency")
        if not current.get("has_active_schedule"):
            overview_flags.append("missing_schedule")
        if advisory := (current.get("advisory_model") or "").lower():
            if advisory == "aua" and not current.get("has_active_rates"):
                overview_flags.append("missing_rates")
        elif not current.get("has_active_rates"):
            overview_flags.append("missing_rates_or_model")

        if missing_only and not (diff["gaps"] or overview_flags) and not note:
            continue

        rows.append(
            {
                "client_id": client.id,
                "client_name": client.name or "",
                "agreement_id": agreement.id,
                "agreement_status": agreement.status or "",
                "special_note": note[:500] + ("…" if len(note) > 500 else ""),
                "confidence": parsed.confidence,
                "would_fill": diff["would_fill"],
                "gaps": ";".join(diff["gaps"] + overview_flags),
                "conflicts": ";".join(diff["conflicts"]),
                "proposed_json": json.dumps(parsed.to_dict(), ensure_ascii=False),
                "parser_flags": ";".join(parsed.flags),
            }
        )

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan agreement special_note vs billing fields")
    parser.add_argument(
        "--csv",
        metavar="PATH",
        help="Write results to CSV file (default: print summary to stdout)",
    )
    parser.add_argument(
        "--confidence",
        choices=("low", "medium", "high"),
        help="Only include rows with this parser confidence",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Focus on clients with gaps, conflicts, or notes but incomplete billing",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        rows = scan_all(
            confidence_filter=args.confidence,
            missing_only=args.missing_only,
        )

    if args.csv:
        fieldnames = list(rows[0].keys()) if rows else [
            "client_id", "client_name", "agreement_id", "agreement_status",
            "special_note", "confidence", "would_fill", "gaps", "conflicts",
            "proposed_json", "parser_flags",
        ]
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} rows to {args.csv}")
    else:
        total = len(rows)
        with_note = sum(1 for r in rows if r.get("special_note"))
        would_fill = sum(1 for r in rows if r.get("would_fill"))
        high = sum(1 for r in rows if r.get("confidence") == "high")
        conflicts = sum(1 for r in rows if r.get("conflicts"))
        print("Agreement special_note scan (dry-run)")
        print(f"  clients/rows:     {total}")
        print(f"  with special_note:{with_note}")
        print(f"  confidence=high:  {high}")
        print(f"  would_fill (safe):{would_fill}")
        print(f"  has conflicts:    {conflicts}")
        print()
        print("Sample rows (up to 15 with notes):")
        shown = 0
        for r in rows:
            if not r.get("special_note"):
                continue
            print("-" * 72)
            print(f"Client {r['client_id']} {r['client_name']} | Agreement #{r['agreement_id']} ({r['agreement_status']})")
            print(f"  confidence={r['confidence']} would_fill={r['would_fill']}")
            if r.get("parser_flags"):
                print(f"  parser_flags: {r['parser_flags']}")
            if r.get("gaps"):
                print(f"  gaps: {r['gaps']}")
            if r.get("conflicts"):
                print(f"  conflicts: {r['conflicts']}")
            print(f"  note: {r['special_note'][:200]}")
            try:
                prop = json.loads(r["proposed_json"])
                compact = {k: v for k, v in prop.items() if v is not None and k != "flags"}
                if compact:
                    print(f"  proposed: {compact}")
            except json.JSONDecodeError:
                pass
            shown += 1
            if shown >= 15:
                break


if __name__ == "__main__":
    main()
