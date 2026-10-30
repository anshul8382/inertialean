#!/usr/bin/env python3
"""Create ia_regulatory_snapshot tables if missing.

Run: python3 migrations/add_ia_regulatory_snapshot.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def main() -> int:
    app = create_app()
    with app.app_context():
        from models.ia_regulatory_snapshot import (
            IaRegulatorySnapshot,
            IaRegulatorySnapshotClientEvent,
        )
        import services.db_cutover as cutover

        IaRegulatorySnapshot.__table__.create(db.engine, checkfirst=True)
        IaRegulatorySnapshotClientEvent.__table__.create(db.engine, checkfirst=True)
        cutover._ia_regulatory_table_checked = None
        print("ia_regulatory_snapshot tables ready")
        print("ia_regulatory_statement_enabled =", cutover.ia_regulatory_statement_enabled())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
