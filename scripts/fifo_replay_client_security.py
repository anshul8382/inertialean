#!/usr/bin/env python3
"""
Terminal FIFO replay for one client + security.

Uses services.capital_gains_service.get_capital_gains_fifo_full_replay — same engine as
get_capital_gains_report. Output focuses on SELL processing (which lots were matched);
BUY step-by-step buildup is not printed. SPLIT/BONUS shown as one line each when they occur.

Example (defaults: Abhijit ray, THYROCARE, FY 2025-26):
  python scripts/fifo_replay_client_security.py

Custom:
  python scripts/fifo_replay_client_security.py --client-name "Abhijit" --symbol THYROCARE --fy-start-year 2025
  python scripts/fifo_replay_client_security.py --as-of 2026-03-31
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import create_app
from models import Client, Security
from services.capital_gains_service import get_capital_gains_fifo_full_replay
from services.fy_tax_utils import fy_bounds_for_start_year


def main() -> None:
    ap = argparse.ArgumentParser(description="FIFO sell matching only (capital gains engine)")
    ap.add_argument("--client-name", default="Abhijit ray", help="Substring match on client.name")
    ap.add_argument("--symbol", default="THYROCARE", help="Security symbol (case-insensitive)")
    ap.add_argument(
        "--fy-start-year",
        type=int,
        default=2025,
        help="India FY start year (e.g. 2025 → FY 2025-26, replay through 31 Mar 2026)",
    )
    ap.add_argument("--as-of", default=None, help="Override end date YYYY-MM-DD (inclusive for txns/CAs)")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        qn = (args.client_name or "").strip()
        clients = Client.query.filter(Client.name.ilike(f"%{qn}%")).limit(10).all()
        if not clients:
            print(f"No client matching name ilike %{qn}%", file=sys.stderr)
            sys.exit(1)
        if len(clients) > 1:
            print(f"Multiple clients ({len(clients)}); narrow --client-name:\n", file=sys.stderr)
            for c in clients:
                print(f"  id={c.id}  {c.name!r}", file=sys.stderr)
            sys.exit(1)
        client = clients[0]

        sym = (args.symbol or "").strip()
        sec = Security.query.filter(Security.symbol.ilike(sym)).first()
        if not sec:
            print(f"No security with symbol ilike {sym!r}", file=sys.stderr)
            sys.exit(1)

        if args.as_of:
            fy_end = date.fromisoformat(args.as_of.strip())
        else:
            _, fy_end = fy_bounds_for_start_year(int(args.fy_start_year))

        rep = get_capital_gains_fifo_full_replay(client.id, sec.id, fy_end)

        print("=" * 72)
        print("FIFO REPLAY (same engine as capital gains report)")
        print("=" * 72)
        print(f"Client:     {rep.get('client_name')} (id={rep.get('client_id')})")
        print(f"Security:   {rep.get('security_symbol')} (id={rep.get('security_id')})")
        print(f"Through:    {rep.get('fy_end')} (inclusive)")
        print(f"LTCG rule:  {rep.get('ltcg_rule_label') or rep.get('ltcg_eligibility_mode')}")
        print(f"Status:     {'ok' if rep.get('ok') else 'error'}")
        if rep.get("error"):
            print(f"Error:      {rep['error']}")
        print()

        steps = rep.get("steps") or []
        print(
            "Per-BUY queue steps are omitted. Each SELL below shows which acquisition date(s) "
            "(and cost) were matched in FIFO order.\n"
        )
        print("— SELL processing (FIFO matching) —")
        sell_n = 0
        for st in steps:
            kind = (st.get("kind") or "").lower()
            if kind == "buy":
                continue
            if kind == "sell":
                sell_n += 1
                print(f"\n--- SELL #{sell_n} — txn {st.get('txn_id')} @ {st.get('date')} ---")
                print(f"    {st.get('summary') or ''}")
                if st.get("fifo_slices"):
                    print("    Matched from open lots (oldest first):")
                    for sl in st["fifo_slices"]:
                        print(
                            f"      • qty {sl.get('quantity'):g} from acquisition {sl.get('matched_acquisition_date')} "
                            f"| cost ₹{sl.get('cost_basis'):,.2f} "
                            f"| sale ₹{sl.get('sale_value'):,.2f} "
                            f"| gain ₹{sl.get('gain'):,.2f} ({sl.get('gain_type')}, {sl.get('holding_days')}d held)"
                        )
                la = (st.get("lots_after") or "").replace("\n", " | ")
                if la.strip():
                    print(f"    Open lots after: {la}")
                continue
            # split / bonus — one line, no queue dump
            if kind in ("split", "bonus"):
                print(f"\n[{kind.upper()}] {st.get('date')}: {st.get('summary') or ''}")
                continue
            print(f"\n--- {kind.upper()} ---")
            print(st.get("summary") or "")

        print("=" * 72)
        print("REMAINING OPEN LOTS (qty & embedded cost) after full replay")
        print("=" * 72)
        rem = rep.get("lots_remaining") or []
        if not rem:
            print("  (none)")
        else:
            tot_q = 0.0
            tot_c = 0.0
            for j, lot in enumerate(rem, 1):
                q = float(lot.get("quantity") or 0)
                ec = float(lot.get("embedded_cost") or 0)
                tot_q += q
                tot_c += ec
                print(
                    f"  #{j} acq {lot.get('acquisition_date')} | "
                    f"qty {q:g} | ₹/unit {lot.get('cost_per_unit'):.4f} | embedded ₹{ec:,.2f}"
                )
            print(f"  TOTAL remaining qty {tot_q:g} | embedded cost ₹{tot_c:,.2f}")
        print()
        print(rep.get("disclaimer") or "")


if __name__ == "__main__":
    main()
