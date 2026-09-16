#!/usr/bin/env python3
"""
Migration: Allow invoice.agreement_id to be NULL (historical imports without a signed agreement).
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text


def upgrade():
    app = create_app()
    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            cols = {c["name"]: c for c in inspector.get_columns("invoice")}
            if "agreement_id" not in cols:
                print("⚠ invoice.agreement_id column not found; skip")
                return True
            if cols["agreement_id"].get("nullable"):
                print("→ agreement_id already nullable")
                return True
            dialect = db.engine.dialect.name
            if dialect == "mysql":
                db.session.execute(
                    text("ALTER TABLE invoice MODIFY agreement_id INT NULL")
                )
            elif dialect == "postgresql":
                db.session.execute(
                    text("ALTER TABLE invoice ALTER COLUMN agreement_id DROP NOT NULL")
                )
            else:
                print(f"⚠ Add manual ALTER for dialect: {dialect}")
                return False
            db.session.commit()
            print("✓ invoice.agreement_id is now nullable")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"❌ Migration failed: {e}")
            raise


def downgrade():
    app = create_app()
    with app.app_context():
        try:
            n = db.session.execute(
                text("SELECT COUNT(*) FROM invoice WHERE agreement_id IS NULL")
            ).scalar()
            if n and int(n) > 0:
                print(
                    "❌ Downgrade aborted: fix NULL agreement_id on invoice rows first "
                    f"({n} row(s))"
                )
                return False
            dialect = db.engine.dialect.name
            if dialect == "mysql":
                db.session.execute(
                    text("ALTER TABLE invoice MODIFY agreement_id INT NOT NULL")
                )
            elif dialect == "postgresql":
                db.session.execute(
                    text("ALTER TABLE invoice ALTER COLUMN agreement_id SET NOT NULL")
                )
            else:
                print(f"⚠ Manual downgrade for dialect: {dialect}")
                return False
            db.session.commit()
            print("✓ invoice.agreement_id is NOT NULL again")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"❌ Downgrade failed: {e}")
            raise


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "downgrade":
        downgrade()
    else:
        upgrade()
