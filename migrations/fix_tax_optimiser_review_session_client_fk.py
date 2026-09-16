#!/usr/bin/env python3
"""
FY-wide tax optimiser sessions used client_id = 0, but a foreign key to client(id) makes that invalid.

- Drop FK on tax_optimiser_review_session.client_id (if present).
- Make client_id NULLable; NULL = all clients / not scoped to one client.
- Backfill: 0 -> NULL; orphan ids -> NULL.
- Re-add FK with ON DELETE SET NULL so NULL rows stay valid.
"""
import os
import sys

from sqlalchemy import inspect, text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from utils.sql_ddl import alter_table_drop_foreign_key


def upgrade():
    app = create_app()
    with app.app_context():
        insp = inspect(db.engine)
        if "tax_optimiser_review_session" not in insp.get_table_names():
            print("tax_optimiser_review_session: table missing, skip")
            return

        rows = db.session.execute(
            text(
                """
                SELECT CONSTRAINT_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'tax_optimiser_review_session'
                  AND REFERENCED_TABLE_NAME = 'client'
                  AND REFERENCED_COLUMN_NAME = 'id'
                """
            )
        ).fetchall()
        fk_names = sorted({r[0] for r in rows if r and r[0]})
        for fk in fk_names:
            alter_table_drop_foreign_key(db.session, "tax_optimiser_review_session", fk)
            print(f"Dropped foreign key {fk}")
        if fk_names:
            db.session.commit()

        db.session.execute(
            text(
                """
                ALTER TABLE tax_optimiser_review_session
                MODIFY COLUMN client_id INT NULL
                """
            )
        )
        db.session.commit()
        print("tax_optimiser_review_session.client_id is now NULLable")

        db.session.execute(
            text(
                """
                UPDATE tax_optimiser_review_session
                SET client_id = NULL
                WHERE client_id = 0
                """
            )
        )
        db.session.execute(
            text(
                """
                UPDATE tax_optimiser_review_session t
                LEFT JOIN client c ON c.id = t.client_id
                SET t.client_id = NULL
                WHERE t.client_id IS NOT NULL AND c.id IS NULL
                """
            )
        )
        db.session.commit()
        print("Backfilled client_id NULL for 0 and orphan ids")

        chk = db.session.execute(
            text(
                """
                SELECT COUNT(*) AS c FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'tax_optimiser_review_session'
                  AND REFERENCED_TABLE_NAME = 'client'
                """
            )
        ).fetchone()
        cnt = int(chk[0]) if chk and chk[0] is not None else 0
        if cnt == 0:
            db.session.execute(
                text(
                    """
                    ALTER TABLE tax_optimiser_review_session
                    ADD CONSTRAINT fk_tors_client
                    FOREIGN KEY (client_id) REFERENCES client(id) ON DELETE SET NULL
                    """
                )
            )
            db.session.commit()
            print("Added fk_tors_client (client_id -> client.id, ON DELETE SET NULL)")
        else:
            print("Foreign key to client already present; skip ADD CONSTRAINT")


if __name__ == "__main__":
    upgrade()
