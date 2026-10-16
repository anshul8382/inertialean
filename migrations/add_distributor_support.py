#!/usr/bin/env python3
"""
Additive migration: distributor role, client.distributor_id, revenue % on user,
distributor_invoice_attribution table.

  python3 migrations/add_distributor_support.py
  python3 migrations/add_distributor_support.py --verify
"""
from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from extensions import db
from main import create_app

DISTRIBUTOR_ENDPOINTS = (
    "distributor.dashboard",
    "distributor.client_summary",
    "distributor.payouts",
    "distributor.pitch_deck",
    "notification_centre.centre",
    "notification_centre_api.list_notifications",
    "notification_centre_api.notification_count",
    "notification_centre_api.decide_notification",
    "notification_centre_api.list_peers",
    "notification_centre_api.send_peer_message",
)

# Staff-only; revoke if previously granted to distributor role
DISTRIBUTOR_REVOKE_ENDPOINTS = (
    "main.user_manual_pdf",
)


def _table_names() -> set[str]:
    return set(inspect(db.engine).get_table_names())


def _columns(table: str) -> set[str]:
    if table not in _table_names():
        return set()
    return {c["name"] for c in inspect(db.engine).get_columns(table)}


def _ensure_role() -> bool:
    from models import Role, RolePermission

    changed = False
    role = Role.query.filter_by(name="distributor").first()
    if not role:
        role = Role(
            name="distributor",
            display_name="Distributor",
            description="View-only access to assigned clients; revenue share on paid invoices",
            is_system_role=True,
            is_active=True,
        )
        db.session.add(role)
        db.session.flush()
        changed = True
        print("✓ Created role distributor")
    for endpoint in DISTRIBUTOR_ENDPOINTS:
        existing = RolePermission.query.filter_by(
            role_id=role.id, route_endpoint=endpoint
        ).first()
        if existing:
            if not existing.has_access:
                existing.has_access = True
                changed = True
        else:
            db.session.add(
                RolePermission(role_id=role.id, route_endpoint=endpoint, has_access=True)
            )
            changed = True
    for endpoint in DISTRIBUTOR_REVOKE_ENDPOINTS:
        existing = RolePermission.query.filter_by(
            role_id=role.id, route_endpoint=endpoint
        ).first()
        if existing and existing.has_access:
            existing.has_access = False
            changed = True
            print(f"✓ Revoked distributor access to {endpoint}")
    return changed


def upgrade() -> bool:
    app = create_app()
    with app.app_context():
        try:
            # user.distributor_revenue_share_percent
            user_cols = _columns("user")
            if "distributor_revenue_share_percent" not in user_cols:
                db.session.execute(
                    text(
                        "ALTER TABLE `user` ADD COLUMN distributor_revenue_share_percent "
                        "NUMERIC(5, 2) NULL DEFAULT NULL"
                    )
                )
                print("✓ Added user.distributor_revenue_share_percent")
            else:
                print("✓ user.distributor_revenue_share_percent exists")

            # client.distributor_id
            client_cols = _columns("client")
            if "distributor_id" not in client_cols:
                db.session.execute(
                    text(
                        "ALTER TABLE `client` ADD COLUMN distributor_id INT NULL, "
                        "ADD INDEX idx_client_distributor_id (distributor_id)"
                    )
                )
                # FK optional on some hosts; try and ignore if unsupported
                try:
                    db.session.execute(
                        text(
                            "ALTER TABLE `client` ADD CONSTRAINT fk_client_distributor "
                            "FOREIGN KEY (distributor_id) REFERENCES `user` (id)"
                        )
                    )
                except Exception as fk_err:
                    print(f"  (FK note) {fk_err}")
                print("✓ Added client.distributor_id")
            else:
                print("✓ client.distributor_id exists")

            if "distributor_invoice_attribution" not in _table_names():
                db.session.execute(
                    text(
                        """
                        CREATE TABLE distributor_invoice_attribution (
                            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                            distributor_id INT NOT NULL,
                            client_id INT NOT NULL,
                            invoice_id INT NOT NULL,
                            revenue_share_percent NUMERIC(5, 2) NOT NULL,
                            invoice_amount_paid NUMERIC(12, 2) NOT NULL,
                            payout_amount NUMERIC(12, 2) NOT NULL,
                            status VARCHAR(20) NOT NULL DEFAULT 'active',
                            paid_date DATE NULL,
                            attributed_at DATETIME NULL,
                            reversed_at DATETIME NULL,
                            notes TEXT NULL,
                            created_at DATETIME NULL,
                            updated_at DATETIME NULL,
                            UNIQUE KEY uq_distributor_attr_invoice (invoice_id),
                            KEY idx_dist_attr_distributor (distributor_id),
                            KEY idx_dist_attr_client (client_id),
                            KEY idx_dist_attr_paid_date (paid_date),
                            KEY idx_dist_attr_status (status)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                        """
                    )
                )
                print("✓ Created distributor_invoice_attribution")
            else:
                print("✓ distributor_invoice_attribution exists")

            if _ensure_role():
                print("✓ Distributor role permissions updated")
            else:
                print("✓ Distributor role already seeded")

            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            print(f"❌ Migration failed: {e}")
            return False


def verify() -> bool:
    app = create_app()
    with app.app_context():
        from models import Role

        ok = True
        if "distributor_revenue_share_percent" not in _columns("user"):
            print("❌ Missing user.distributor_revenue_share_percent")
            ok = False
        else:
            print("✓ user.distributor_revenue_share_percent")
        if "distributor_id" not in _columns("client"):
            print("❌ Missing client.distributor_id")
            ok = False
        else:
            print("✓ client.distributor_id")
        if "distributor_invoice_attribution" not in _table_names():
            print("❌ Missing distributor_invoice_attribution")
            ok = False
        else:
            print("✓ distributor_invoice_attribution")
        if not Role.query.filter_by(name="distributor").first():
            print("❌ Missing distributor role")
            ok = False
        else:
            print("✓ distributor role")
        return ok


if __name__ == "__main__":
    if "--verify" in sys.argv:
        raise SystemExit(0 if verify() else 1)
    raise SystemExit(0 if upgrade() else 1)
