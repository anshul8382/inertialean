#!/usr/bin/env python3
"""Merge require_fy_stcg_loss_offset + max_allowed_incremental_stcg_tax_inr into book_stcg_gains params if missing."""
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text


def upgrade():
    app = create_app()
    with app.app_context():
        row = db.session.execute(
            text("SELECT id, params FROM tax_optimiser_strategy WHERE code = :c LIMIT 1"),
            {"c": "book_stcg_gains"},
        ).fetchone()
        if not row:
            print("book_stcg_gains row missing — skip")
            return
        rid, raw = row[0], row[1]
        try:
            d = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except Exception:
            d = {}
        changed = False
        if "require_fy_stcg_loss_offset" not in d:
            d["require_fy_stcg_loss_offset"] = True
            changed = True
        if "max_allowed_incremental_stcg_tax_inr" not in d:
            d["max_allowed_incremental_stcg_tax_inr"] = 0
            changed = True
        if changed:
            db.session.execute(
                text("UPDATE tax_optimiser_strategy SET params = :p WHERE id = :id"),
                {"p": json.dumps(d), "id": rid},
            )
            db.session.commit()
            print("Updated book_stcg_gains params")
        else:
            print("book_stcg_gains params already have offset keys")


if __name__ == "__main__":
    upgrade()
