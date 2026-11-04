#!/usr/bin/env python3
"""Widen security.symbol from VARCHAR(20) to VARCHAR(50) for MF codes / ISINs."""

from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text


def _symbol_char_max(conn) -> int | None:
    row = conn.execute(
        text(
            """
            SELECT CHARACTER_MAXIMUM_LENGTH
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'security'
              AND column_name = 'symbol'
            """
        )
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def upgrade() -> bool:
    app = create_app()
    with app.app_context():
        try:
            length = _symbol_char_max(db.session)
            if length is None:
                print("✗ security.symbol column not found")
                return False
            if length >= 50:
                print(f"→ security.symbol already VARCHAR({length}); nothing to do")
                return True

            print(f"Widening security.symbol VARCHAR({length}) → VARCHAR(50)…")
            db.session.execute(
                text(
                    """
                    ALTER TABLE security
                    MODIFY COLUMN symbol VARCHAR(50) NOT NULL
                    """
                )
            )
            db.session.commit()
            print("✅ security.symbol is now VARCHAR(50)")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"❌ Migration failed: {e}")
            import traceback

            traceback.print_exc()
            return False


if __name__ == "__main__":
    ok = upgrade()
    sys.exit(0 if ok else 1)
