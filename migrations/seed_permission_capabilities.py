#!/usr/bin/env python3
"""
Seed capability:* permissions on Role rows (idempotent).

Run after deploy or when adding new capabilities:
  python3 migrations/seed_permission_capabilities.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from main import create_app
    from extensions import db
    from models import Role, RolePermission
    from services.permission_service import (
        CAP_HOLDINGS_REPORT,
        CAP_INCENTIVE_SIMULATOR,
        CAP_PRACTICE_ANALYTICS,
        CAP_CLIENT_DATA_OPS,
        CAP_CLIENT_DATA_SENSITIVE,
    )

    app = create_app()
    with app.app_context():
        role_caps = {
            "admin": [CAP_INCENTIVE_SIMULATOR, CAP_HOLDINGS_REPORT, CAP_PRACTICE_ANALYTICS, CAP_CLIENT_DATA_OPS, CAP_CLIENT_DATA_SENSITIVE],
            "manager": [CAP_INCENTIVE_SIMULATOR, CAP_HOLDINGS_REPORT, CAP_PRACTICE_ANALYTICS, CAP_CLIENT_DATA_OPS, CAP_CLIENT_DATA_SENSITIVE],
            "advisor": [CAP_INCENTIVE_SIMULATOR, CAP_HOLDINGS_REPORT, CAP_CLIENT_DATA_OPS, CAP_CLIENT_DATA_SENSITIVE],
            "ops_manager": [CAP_HOLDINGS_REPORT, CAP_CLIENT_DATA_OPS],
            "sales_ops_manager": [CAP_INCENTIVE_SIMULATOR, CAP_HOLDINGS_REPORT],
            "sales": [CAP_INCENTIVE_SIMULATOR],
            "sales_executive": [CAP_INCENTIVE_SIMULATOR],
        }
        added = 0
        for role_name, caps in role_caps.items():
            role = Role.query.filter_by(name=role_name).first()
            if not role:
                continue
            for cap in caps:
                existing = RolePermission.query.filter_by(
                    role_id=role.id, route_endpoint=cap
                ).first()
                if existing:
                    if not existing.has_access:
                        existing.has_access = True
                        added += 1
                    continue
                db.session.add(
                    RolePermission(role_id=role.id, route_endpoint=cap, has_access=True)
                )
                added += 1
        db.session.commit()
        print(f"Capability permissions seeded/updated ({added} changes).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
