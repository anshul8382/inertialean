#!/usr/bin/env python3
"""Add client_bni_referral and bni_ty_notes_state for BNI TY Notes weekly report."""
import os
import sys

from sqlalchemy import inspect, text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def _table_exists(name: str) -> bool:
    inspector = inspect(db.engine)
    return name in inspector.get_table_names()


def upgrade():
    app = create_app()
    with app.app_context():
        if not _table_exists("client_bni_referral"):
            db.session.execute(
                text(
                    """
                CREATE TABLE client_bni_referral (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    client_id INT NOT NULL UNIQUE,
                    referral_name VARCHAR(255) NOT NULL DEFAULT '',
                    created_at DATETIME NULL,
                    updated_at DATETIME NULL,
                    CONSTRAINT fk_client_bni_referral_client
                        FOREIGN KEY (client_id) REFERENCES client (id) ON DELETE CASCADE
                )
                """
                )
            )
            db.session.commit()
            print("Created client_bni_referral")
        else:
            print("client_bni_referral already exists")

        if not _table_exists("bni_ty_notes_state"):
            db.session.execute(
                text(
                    """
                CREATE TABLE bni_ty_notes_state (
                    id INT NOT NULL PRIMARY KEY,
                    last_success_end_at DATETIME NULL,
                    updated_at DATETIME NULL
                )
                """
                )
            )
            db.session.execute(text("INSERT INTO bni_ty_notes_state (id) VALUES (1)"))
            db.session.commit()
            print("Created bni_ty_notes_state")
        else:
            print("bni_ty_notes_state already exists")


if __name__ == "__main__":
    upgrade()
