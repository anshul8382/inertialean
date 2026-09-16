#!/usr/bin/env python3
"""
Seed research role with menu allowlist + client-data capabilities (idempotent).

Run after deploy:
  python3 migrations/seed_research_role.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RESEARCH_MENU_ENDPOINTS = (
    "main.securities",
    "main.models",
    "main.list_asset_classes",
    "main.maintenance_reference_data",
    "financial_analytics.dashboard",
    "assistant.assistant_hub",
    "assistant.help_assistant_page",
    "main.user_manual_pdf",
)


def _ensure_permission(role_id: int, endpoint: str) -> bool:
    from extensions import db
    from models import RolePermission

    existing = RolePermission.query.filter_by(role_id=role_id, route_endpoint=endpoint).first()
    if existing:
        changed = False
        if not existing.has_access:
            existing.has_access = True
            changed = True
        return changed
    db.session.add(RolePermission(role_id=role_id, route_endpoint=endpoint, has_access=True))
    return True


def main() -> int:
    from main import create_app
    from extensions import db
    from models import Role
    from services.permission_service import CAP_CLIENT_DATA_OPS

    app = create_app()
    with app.app_context():
        role = Role.query.filter_by(name="research").first()
        if not role:
            role = Role(
                name="research",
                display_name="Research",
                description="Read-only research access to reference data and analytics",
                is_system_role=True,
                is_active=True,
            )
            db.session.add(role)
            db.session.flush()

        changes = 0
        for endpoint in RESEARCH_MENU_ENDPOINTS:
            if _ensure_permission(role.id, endpoint):
                changes += 1
        if _ensure_permission(role.id, CAP_CLIENT_DATA_OPS):
            changes += 1

        db.session.commit()
        print(f"Research role seeded ({changes} permission changes).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
