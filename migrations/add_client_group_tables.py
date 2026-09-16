#!/usr/bin/env python3
"""Migration: client_group + client_group_member for household consolidated views."""

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
            if not inspector.has_table("client_group"):
                db.session.execute(text("""
                    CREATE TABLE client_group (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(200) NOT NULL,
                        primary_client_id INT NOT NULL,
                        notes TEXT NULL,
                        created_by INT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        CONSTRAINT fk_cg_primary_client FOREIGN KEY (primary_client_id) REFERENCES client(id),
                        CONSTRAINT fk_cg_created_by FOREIGN KEY (created_by) REFERENCES user(id),
                        INDEX idx_cg_primary (primary_client_id)
                    )
                """))
                print("✓ Created client_group table")
            else:
                print("→ client_group table already exists")

            if not inspector.has_table("client_group_member"):
                db.session.execute(text("""
                    CREATE TABLE client_group_member (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        group_id INT NOT NULL,
                        client_id INT NOT NULL,
                        role VARCHAR(30) NOT NULL DEFAULT 'other',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT fk_cgm_group FOREIGN KEY (group_id) REFERENCES client_group(id) ON DELETE CASCADE,
                        CONSTRAINT fk_cgm_client FOREIGN KEY (client_id) REFERENCES client(id),
                        CONSTRAINT uq_cgm_client UNIQUE (client_id),
                        INDEX idx_cgm_group (group_id),
                        INDEX idx_cgm_client (client_id)
                    )
                """))
                print("✓ Created client_group_member table")
            else:
                print("→ client_group_member table already exists")

            db.session.commit()
            print("\n✅ Migration completed successfully!")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Migration failed: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == "__main__":
    upgrade()
