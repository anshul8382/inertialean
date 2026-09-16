#!/usr/bin/env python3
"""
Report clients where lifetime recorded cashflow net differs from trade-implied net.

Rules:
  - Compare Cashflow net vs post-cutoff BUY/SELL net (exclude 2000-01-01 opening book).
  - Materiality default: max(1% of |recorded_net|, ₹1,000). Use --tolerance for absolute override.
  - Opening-book with post-cutoff OK → ignore_opening_book (not a mismatch).
  - Day-level suspects use ±14d clubbing before orphan/missing flags.

Usage (from project root):
  python3 scripts/report_cashflow_trade_mismatches.py
  python3 scripts/report_cashflow_trade_mismatches.py --material-only --csv docs/
  python3 scripts/report_cashflow_trade_mismatches.py --tolerance 10000 --csv docs/
  python3 scripts/report_cashflow_trade_mismatches.py --client-id 42
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _fmt_inr(x: float) -> str:
    try:
        return f"₹{float(x):,.2f}"
    except (TypeError, ValueError):
        return "₹0.00"


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _print_table(title: str, rows: list[dict], show_guess: bool = False) -> None:
    print()
    print("=" * 88)
    print(title)
    print("=" * 88)
    if not rows:
        print("(none)")
        return
    header = (
        f"{'ID':>6}  {'Client':<24}  {'Status':<20}  {'Recorded net':>14}  "
        f"{'Post CF gap':>14}  {'Series':>8}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        name = (r.get("client_name") or "")[:24]
        status = (r.get("status") or "")[:20]
        series = "OK" if r.get("series_matched", status in ("matched", "ignore_opening_book")) else "REVIEW"
        if "series_matched" not in r and r.get("has_large_day_review"):
            series = "REVIEW"
        print(
            f"{r['client_id']:>6}  {name:<24}  {status:<20}  "
            f"{_fmt_inr(r['recorded_net_cashflow']):>14}  "
            f"{_fmt_inr(r['difference']):>14}  "
            f"{series:>8}"
        )
        if show_guess and r.get("guess_summary"):
            print(f"         guess: {r['guess_summary']}")
        if show_guess and r.get("match_explanation"):
            print(f"         note: {(r.get('match_explanation') or '')[:160]}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--tolerance",
        type=float,
        default=None,
        help="Absolute ₹ override (default: max(1%% of |recorded_net|, 1000))",
    )
    p.add_argument(
        "--skip-date",
        default="2000-01-01",
        metavar="YYYY-MM-DD",
        help="Opening-book trade date excluded from post-cutoff net (default: 2000-01-01)",
    )
    p.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive clients (default: active only)",
    )
    p.add_argument("--client-id", type=int, action="append", dest="client_ids", help="Limit to client id(s)")
    p.add_argument(
        "--material-only",
        action="store_true",
        help="Only list mismatch_material (skip mismatch_review)",
    )
    p.add_argument(
        "--csv",
        metavar="DIR",
        default=None,
        help="Write matched / mismatch / opening-book / suspects CSVs into this directory",
    )
    p.add_argument("--json", action="store_true", help="Print full JSON report to stdout")
    p.add_argument(
        "--no-diagnose",
        action="store_true",
        help="Skip day-level suspect guessing on mismatches (faster)",
    )
    args = p.parse_args()

    skip_date = datetime.strptime(args.skip_date.strip(), "%Y-%m-%d").date()

    from main import create_app
    from services.cashflow_trade_mismatch_report_service import build_totals_mismatch_report

    app = create_app()
    with app.app_context():
        report = build_totals_mismatch_report(
            client_ids=args.client_ids,
            active_only=not args.include_inactive,
            tolerance=args.tolerance,
            skip_date=skip_date,
            diagnose_mismatches=not args.no_diagnose,
            material_only=args.material_only,
        )

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    print("Cashflow vs trade totals mismatch report")
    print(f"  Generated:     {report['generated_at']}")
    tol = report.get("tolerance")
    if tol is None:
        print(
            f"  Materiality:   max({100 * float(report.get('materiality_pct') or 0.01):.0f}% "
            f"|recorded|, {_fmt_inr(report.get('materiality_floor') or 1000)})"
        )
    else:
        print(f"  Tolerance:     {_fmt_inr(tol)}")
    print(f"  Skip trade date: {report['skip_trade_date']}")
    print(f"  Active only:   {report['active_only']}")
    print(f"  Considered:    {report['clients_considered']}")
    print(f"  Matched (OK):  {report['matched_count']}")
    print(f"  Mismatches:    {report['mismatch_count']}")
    print(f"  Ignore opening book: {report.get('ignore_opening_book_count', report.get('skipped_dummy_count'))}")
    print(f"  Suspect day rows: {len(report.get('mismatch_suspects') or [])}")

    _print_table(
        "MISMATCHES (material / review — post-cutoff)",
        report["mismatches"],
        show_guess=True,
    )
    _print_table(
        f"IGNORE OPENING BOOK — post-cutoff OK (trades on {report['skip_trade_date']})",
        report.get("ignore_opening_book") or report.get("skipped_dummy_trades") or [],
    )
    _print_table(
        "MATCHED (no opening book; post-cutoff within materiality)",
        report.get("matched") or [],
    )

    if args.csv is not None:
        out_dir = Path(args.csv)
        stamp = date.today().isoformat()
        summary_fields = [
            "client_id",
            "client_name",
            "recorded_net_cashflow",
            "trade_net_cashflow",
            "difference",
            "abs_difference",
            "cashflow_count",
            "buy_sell_count",
            "status",
            "series_matched",
            "has_large_day_review",
            "skip_trade_date",
            "guess_summary",
            "orphan_cashflow_days",
            "missing_cashflow_days",
            "amount_mismatch_days",
            "explained_by_suspect_days",
        ]
        suspect_fields = [
            "client_id",
            "client_name",
            "total_difference",
            "date",
            "issue",
            "cashflow_net",
            "trade_net",
            "day_difference",
            "cashflow_ids",
            "transaction_ids",
            "guess",
        ]
        paths = [
            (out_dir / f"cashflow_trade_matched_{stamp}.csv", report.get("matched") or [], summary_fields),
            (out_dir / f"cashflow_trade_mismatches_{stamp}.csv", report["mismatches"], summary_fields),
            (
                out_dir / f"cashflow_trade_skipped_dummy_{stamp}.csv",
                report["skipped_dummy_trades"],
                summary_fields,
            ),
            (
                out_dir / f"cashflow_trade_mismatch_suspects_{stamp}.csv",
                report.get("mismatch_suspects") or [],
                suspect_fields,
            ),
        ]
        print()
        for path, rows, fields in paths:
            _write_csv(path, rows, fields)
            print(f"Wrote {path} ({len(rows)} rows)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
