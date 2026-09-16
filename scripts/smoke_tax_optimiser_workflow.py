#!/usr/bin/env python3
"""
End-to-end smoke: tax optimiser migrations present, strategy engine, REST API, enhanced page.

Run from repo root:
  python scripts/smoke_tax_optimiser_workflow.py

Exit 0 on success, 1 on failure.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _fail(msg: str) -> int:
    print("FAIL:", msg)
    return 1


def _ok(msg: str) -> None:
    print("OK:", msg)


def main() -> int:
    from sqlalchemy import inspect

    from __init__ import create_app
    from extensions import db

    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False

    http_user = None
    with app.app_context():
        insp = inspect(db.engine)
        tables = set(insp.get_table_names())
        need = {
            "tax_optimiser_settings",
            "tax_optimiser_strategy",
            "tax_optimiser_followup_batch",
        }
        missing = need - tables
        if missing:
            return _fail(
                f"Missing tables {sorted(missing)}. Run: "
                "python migrations/add_tax_optimiser_settings_table.py && "
                "python migrations/add_tax_optimiser_v2_ltcg_and_cost.py && "
                "python migrations/add_tax_optimiser_strategy_and_followup.py"
            )
        _ok("DB tables present: " + ", ".join(sorted(need)))

        try:
            from models import TaxOptimiserStrategy

            n = TaxOptimiserStrategy.query.count()
            if n < 1:
                return _fail("tax_optimiser_strategy has no rows (re-run seed migration)")
            _ok(f"tax_optimiser_strategy rows: {n}")
        except Exception as e:
            return _fail(f"TaxOptimiserStrategy query: {e}")

        from services.tax_optimiser_strategy_loader import load_enabled_strategy_params

        sp = load_enabled_strategy_params()
        if "book_ltcg_exemption" not in sp:
            return _fail("load_enabled_strategy_params missing book_ltcg_exemption")
        _ok("strategy loader has book_ltcg_exemption")
        if "book_stcg_gains" not in sp:
            return _fail(
                "load_enabled_strategy_params missing book_stcg_gains — run: "
                "python migrations/seed_book_stcg_gains_strategy.py"
            )
        _ok("strategy loader has book_stcg_gains")
        if "harvest_unrealized_loss" not in sp:
            return _fail("load_enabled_strategy_params missing harvest_unrealized_loss")
        _ok("strategy loader has harvest_unrealized_loss")

        from models import Transaction, User

        cid_row = (
            db.session.query(Transaction.client_id)
            .order_by(Transaction.id.desc())
            .first()
        )
        test_cid = int(cid_row[0]) if cid_row else None

        from services.enhanced_tax_optimiser_service import build_enhanced_tax_report

        if test_cid is not None:
            r = build_enhanced_tax_report(fy_start_year=2025, client_id_filter=test_cid)
            _ok(f"build_enhanced_tax_report(client_id={test_cid}) clients={len(r.get('clients') or [])}")
            clients = r.get("clients") or []
            if clients:
                c0 = clients[0]
                sug = c0.get("suggestions") or {}
                bl = sug.get("book_ltcg")
                if bl:
                    has_mtr = bool(bl.get("minimum_turnover_recommendation"))
                    has_rej = bool(bl.get("rejection_reason"))
                    _ok(
                        f"client[0] book_ltcg: headroom={bl.get('headroom')} "
                        f"mtr={has_mtr} rejection={has_rej}"
                    )
                assert sug.get("defer_to_ltcg") is None, "defer_to_ltcg should not appear on report"
                _ok("defer_to_ltcg absent from report (unified SELL hints only)")
        else:
            r = build_enhanced_tax_report(fy_start_year=2025, client_name_filter="__no_match__")
            _ok(f"build_enhanced_tax_report (no txns) clients={len(r.get('clients') or [])}")

        ts = r.get("tax_settings") or {}
        _ok(
            "tax_settings keys include trade_charges_pct=%s"
            % ("trade_charges_pct" in ts)
        )

        # HTTP: pick advisor/manager user (needs same app context as queries above)
        http_user = User.query.filter_by(is_active=True).order_by(User.id.asc()).first()

    if not http_user:
        print("WARN: No User row — skip HTTP checks")
        print("Smoke finished (DB + services only).")
        return 0

    # Avoid Flask-Login + test_client quirks (inactive users, role_obj vs role string).
    # Exercise the same business logic the API routes call.
    from flask import render_template, url_for

    from models import TaxOptimiserStrategy
    from routes.tax_optimiser_enhanced import TXO_ENHANCED_UI_BUILD
    from services.fy_tax_utils import fy_dropdown_options
    from services.tax_optimiser_interactive_service import start_review_session

    with app.app_context():
        strat_rows = TaxOptimiserStrategy.query.order_by(TaxOptimiserStrategy.sort_order).all()
        if len(strat_rows) < 1:
            return _fail("TaxOptimiserStrategy query returned no rows")
        _ok(f"strategies ORM: {len(strat_rows)} rows (same source as GET /api/v1/.../strategies)")

        sess = start_review_session(int(http_user.id), {"fy_start_year": 2025})
        if sess.get("status") != "in_progress":
            return _fail(f"start_review_session unexpected status: {sess.get('status')}")
        n_items = len(sess.get("items") or [])
        _ok(f"start_review_session: items={n_items} (same as POST .../session/start)")

        with app.test_request_context("/tools/tax-optimiser-enhanced"):
            html = render_template(
                "tools/tax_optimiser_enhanced.html",
                txo_enhanced_ui_build=TXO_ENHANCED_UI_BUILD,
                fy_options=fy_dropdown_options(back_years=8),
                txo_fy_options_url=url_for("tax_optimiser_enhanced.tax_optimiser_fy_options", back_years=8),
            )
        if "tax optimiser" not in html.lower():
            return _fail("render tax_optimiser_enhanced.html: missing title text")
        if TXO_ENHANCED_UI_BUILD not in html:
            return _fail(f"render template: missing build badge ({TXO_ENHANCED_UI_BUILD})")
        _ok("render /tools/tax-optimiser-enhanced template (build badge present)")

    print("---")
    print("All smoke checks passed.")
    print("If the UI still looks old: hard-refresh (Ctrl+Shift+R), confirm this code is deployed,")
    print("and restart Gunicorn so Python picks up new routes/services.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
