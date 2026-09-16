#!/usr/bin/env python3
"""
Migration: client_security_advisory_exclusion — per-client holdings excluded from
advisory AUA (billing + practice dashboard) without affecting portfolio/recommendations.
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
            if inspector.has_table("client_security_advisory_exclusion"):
                print("→ client_security_advisory_exclusion table already exists")
                return True

            db.session.execute(
                text(
                    """
                    CREATE TABLE client_security_advisory_exclusion (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        client_id INT NOT NULL,
                        security_id INT NOT NULL,
                        exclude_from_billing BOOLEAN NOT NULL DEFAULT TRUE,
                        exclude_from_practice_aua BOOLEAN NOT NULL DEFAULT TRUE,
                        reason VARCHAR(255) NULL,
                        notes TEXT NULL,
                        created_by INT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        CONSTRAINT fk_csae_client FOREIGN KEY (client_id) REFERENCES client(id),
                        CONSTRAINT fk_csae_security FOREIGN KEY (security_id) REFERENCES security(id),
                        CONSTRAINT fk_csae_created_by FOREIGN KEY (created_by) REFERENCES user(id),
                        CONSTRAINT uq_client_security_advisory_exclusion UNIQUE (client_id, security_id),
                        INDEX idx_csae_client (client_id),
                        INDEX idx_csae_security (security_id)
                    )
                    """
                )
            )
            db.session.commit()
            print("✓ Created client_security_advisory_exclusion table")
            print("\n✅ Migration completed successfully!")
            return True

        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Migration failed: {str(e)}")
            import traceback

            traceback.print_exc()
            return False


def downgrade():
    app = create_app()

    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            if inspector.has_table("client_security_advisory_exclusion"):
                db.session.execute(text("DROP TABLE client_security_advisory_exclusion"))
                print("✓ Dropped client_security_advisory_exclusion table")
            else:
                print("→ client_security_advisory_exclusion table does not exist")

            db.session.commit()
            print("\n✅ Rollback completed successfully!")
            return True

        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Rollback failed: {str(e)}")
            import traceback

            traceback.print_exc()
            return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "downgrade":
        print("Rolling back client_security_advisory_exclusion migration...")
        downgrade()
    else:
        print("Running client_security_advisory_exclusion migration...")
        upgrade()
