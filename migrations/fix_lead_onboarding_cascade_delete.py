#!/usr/bin/env python3
"""Add ON DELETE CASCADE/SET NULL to lead onboarding FKs so lead delete cannot fail.

Run: python migrations/fix_lead_onboarding_cascade_delete.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from main import create_app
from extensions import db

TABLES = (
    ("lead_kyc_profile", "CASCADE"),
    ("lead_proposal", "CASCADE"),
    ("risk_assessment_submission", "SET NULL"),
)


def _fk_names(inspector, table_name):
    if table_name not in inspector.get_table_names():
        return []
    names = []
    for fk in inspector.get_foreign_keys(table_name):
        referred = (fk.get("referred_table") or "").lower()
        cols = [c.lower() for c in (fk.get("constrained_columns") or [])]
        if referred == "lead" and "lead_id" in cols and fk.get("name"):
            names.append(fk["name"])
    return names


def main():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        for table_name, on_delete in TABLES:
            if table_name not in inspector.get_table_names():
                print(f"skip {table_name} (missing)")
                continue
            for fk_name in _fk_names(inspector, table_name):
                db.session.execute(text(f"ALTER TABLE `{table_name}` DROP FOREIGN KEY `{fk_name}`"))
                db.session.execute(
                    text(
                        f"ALTER TABLE `{table_name}` "
                        f"ADD CONSTRAINT `{fk_name}` FOREIGN KEY (`lead_id`) "
                        f"REFERENCES `lead` (`id`) ON DELETE {on_delete}"
                    )
                )
                print(f"{table_name}.{fk_name} -> ON DELETE {on_delete}")
            db.session.commit()
            # Refresh inspector after ALTERs so subsequent tables see current schema
            inspector = inspect(db.engine)
        print("lead onboarding FK cascade ready")


if __name__ == "__main__":
    main()
