#!/usr/bin/env python3
"""
Refresh due tax_optimiser_followup_batch rows: re-read Security.current_price,
regenerate draft_html_refreshed, leave status pending for advisor send.
Run from cron or Airflow (see airflow/dags/tax_optimiser_followup_dag.py).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def main(limit: int = 50) -> None:
    app = create_app()
    with app.app_context():
        from sqlalchemy import inspect
        from models import Security, TaxOptimiserFollowupBatch
        from flask import render_template

        if not inspect(db.engine).has_table("tax_optimiser_followup_batch"):
            print(
                "SKIP: tax_optimiser_followup_batch missing — "
                "run migrations/add_tax_optimiser_strategy_and_followup.py when ready."
            )
            return

        now = _utc_now()
        try:
            q = (
                TaxOptimiserFollowupBatch.query.filter(
                    TaxOptimiserFollowupBatch.status == "pending",
                    TaxOptimiserFollowupBatch.run_after <= now,
                )
                .order_by(TaxOptimiserFollowupBatch.run_after.asc())
                .limit(limit)
            )
            batches = q.all()
        except Exception as exc:
            db.session.rollback()
            print(f"SKIP: followup query failed ({exc})")
            return

        for b in batches:
            snap = dict(b.price_snapshot_json or {})
            new_snap = {}
            for k, v in snap.items():
                if not isinstance(v, dict):
                    continue
                sid = v.get("security_id")
                if sid is None:
                    try:
                        sid = int(k)
                    except (TypeError, ValueError):
                        continue
                sec = db.session.get(Security, int(sid))
                px = float(sec.current_price or 0) if sec else float(v.get("price") or 0)
                sym = (sec.symbol if sec else None) or v.get("symbol") or ""
                new_snap[str(sid)] = {
                    "security_id": int(sid),
                    "symbol": sym,
                    "price": px,
                    "as_of": _utc_now().date().isoformat(),
                    "source": "refresh_script",
                    "prior_price": v.get("price"),
                }
            # Context processors touch flask.session / url_for — need a request context.
            with app.test_request_context("/"):
                html = render_template(
                    "email/tax_optimiser_followup_draft.html",
                    approved_items=b.approved_items_json or [],
                    price_snapshot=new_snap,
                    generated_at=_utc_now().isoformat() + "Z",
                )
            b.draft_html_refreshed = html
            b.updated_at = _utc_now()
        if batches:
            db.session.commit()
        print(f"Refreshed {len(batches)} batch(es).")


if __name__ == "__main__":
    main()
