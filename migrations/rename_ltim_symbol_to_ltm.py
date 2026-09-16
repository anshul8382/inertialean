#!/usr/bin/env python3
"""
Rename NSE listing LTIM -> LTM on security and MProfit map (idempotent).

- Updates security.symbol where it is LTIM.
- Updates mprofit_symbol_map.nse_symbol for rows pointing at that security or nse_symbol LTIM.

After this, Google Sheet column ``Symbol`` must use LTM so the price cron can match
``Security.query.filter_by(symbol=...)`` (see api/v1/cron_jobs.py).

Run: python migrations/rename_ltim_symbol_to_ltm.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import or_

from main import create_app
from extensions import db
from models import Security, MProfitSymbolMap


def run():
    app = create_app()
    with app.app_context():
        sec = Security.query.filter(
            db.func.upper(db.func.trim(Security.symbol)) == "LTIM"
        ).first()
        if not sec:
            s2 = Security.query.filter(
                db.func.upper(db.func.trim(Security.symbol)) == "LTM"
            ).first()
            if s2 and "mindtree" in (s2.name or "").lower():
                print(f"Already LTM: id={s2.id} symbol={s2.symbol!r} name={s2.name!r}")
            else:
                print("No security with symbol LTIM found; nothing to do.")
            return

        sid = sec.id
        print(f"Updating security id={sid}: symbol LTIM -> LTM ({sec.name!r})")
        sec.symbol = "LTM"
        db.session.add(sec)

        maps = MProfitSymbolMap.query.filter(
            or_(
                MProfitSymbolMap.security_id == sid,
                db.func.upper(db.func.trim(MProfitSymbolMap.nse_symbol)) == "LTIM",
            )
        ).all()
        for m in maps:
            if (m.nse_symbol or "").upper().strip() == "LTIM":
                print(f"  mprofit_symbol_map id={m.id}: nse_symbol LTIM -> LTM")
                m.nse_symbol = "LTM"
                db.session.add(m)

        db.session.commit()
        print("Done. Update Google Sheet 'Prices' Symbol column LTIM -> LTM if not already.")


if __name__ == "__main__":
    run()
