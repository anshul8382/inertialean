#!/usr/bin/env python3
"""
Create Role `sales_ops_manager` for users who should have both:
- external (sales) incentive simulator, and
- internal (ops) incentive simulator,
plus the same ops workflow treatment as `ops_manager` (see access_control.user_is_ops_manager).

Assign in Admin → Users: set the user’s Role to “Sales & Ops Manager” (this row).

Run:
  python migrations/seed_sales_ops_manager_role.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from models import Role

SLUG = "sales_ops_manager"


def run():
    app = create_app()
    with app.app_context():
        existing = Role.query.filter_by(name=SLUG).first()
        if existing:
            print(f"Role '{SLUG}' already exists (id={existing.id}). No change.")
            return existing

        role = Role(
            name=SLUG,
            display_name="Sales & Ops Manager",
            description=(
                "Combined role: treated as ops_manager for ops workflows and internal incentives; "
                "also sees external sales incentive UI. Clone permissions from ops_manager in "
                "/users/roles if needed."
            ),
            is_system_role=False,
            parent_role_id=None,
            is_active=True,
        )
        db.session.add(role)
        db.session.commit()
        print(f"Created role '{SLUG}' (id={role.id}). Assign user.role_id in Users admin.")
        return role


if __name__ == "__main__":
    run()
