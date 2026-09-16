#!/usr/bin/env python3
"""Merge prioritize_section_94_8_bonus_window into harvest_unrealized_loss params if missing."""
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
            {"c": "harvest_unrealized_loss"},
        ).fetchone()
        if not row:
            print("harvest_unrealized_loss row missing — skip")
            return
        rid, raw = row[0], row[1]
        try:
            d = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except Exception:
            d = {}
        if "prioritize_section_94_8_bonus_window" not in d:
            d["prioritize_section_94_8_bonus_window"] = True
            db.session.execute(
                text("UPDATE tax_optimiser_strategy SET params = :p WHERE id = :id"),
                {"p": json.dumps(d), "id": rid},
            )
            db.session.commit()
            print("Updated harvest_unrealized_loss params")
        else:
            print("harvest_unrealized_loss already has prioritize_section_94_8_bonus_window")


if __name__ == "__main__":
    upgrade()
