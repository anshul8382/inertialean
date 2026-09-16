#!/usr/bin/env python3
"""Migration: price accuracy audit (historical spikes, missing tx dates, zeros)."""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text
from utils.sql_ddl import drop_table_if_exists


def upgrade():
    app = create_app()

    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            existing = inspector.get_table_names()

            if "price_accuracy_run" not in existing:
                db.session.execute(
                    text(
                        """
                        CREATE TABLE price_accuracy_run (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            started_at DATETIME NOT NULL,
                            finished_at DATETIME NULL,
                            threshold_pct DECIMAL(6, 2) NOT NULL DEFAULT 10.00,
                            securities_scanned INT NOT NULL DEFAULT 0,
                            spike_count INT NOT NULL DEFAULT 0,
                            missing_count INT NOT NULL DEFAULT 0,
                            zero_count INT NOT NULL DEFAULT 0,
                            error_message VARCHAR(500) NULL,
                            INDEX idx_started_at (started_at)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                        """
                    )
                )
                print("✓ Created price_accuracy_run")

            if "price_accuracy_finding" not in existing:
                db.session.execute(
                    text(
                        """
                        CREATE TABLE price_accuracy_finding (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            run_id INT NOT NULL,
                            kind VARCHAR(20) NOT NULL,
                            security_id INT NOT NULL,
                            symbol VARCHAR(40) NOT NULL,
                            date_prev DATE NULL,
                            date_next DATE NULL,
                            close_prev DECIMAL(18, 6) NULL,
                            close_next DECIMAL(18, 6) NULL,
                            pct_change DECIMAL(12, 6) NULL,
                            missing_date DATE NULL,
                            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (run_id) REFERENCES price_accuracy_run(id) ON DELETE CASCADE,
                            FOREIGN KEY (security_id) REFERENCES security(id) ON DELETE CASCADE,
                            INDEX idx_run_kind (run_id, kind),
                            INDEX idx_security (security_id)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                        """
                    )
                )
                print("✓ Created price_accuracy_finding")

            if "price_accuracy_spike_ack" not in existing:
                db.session.execute(
                    text(
                        """
                        CREATE TABLE price_accuracy_spike_ack (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            security_id INT NOT NULL,
                            date_prev DATE NOT NULL,
                            date_next DATE NOT NULL,
                            source VARCHAR(30) NOT NULL DEFAULT 'ui',
                            notes VARCHAR(500) NULL,
                            user_id INT NULL,
                            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE KEY uq_spike_ack (security_id, date_prev, date_next),
                            FOREIGN KEY (security_id) REFERENCES security(id) ON DELETE CASCADE,
                            FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE SET NULL,
                            INDEX idx_security_dates (security_id, date_prev, date_next)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                        """
                    )
                )
                print("✓ Created price_accuracy_spike_ack")

            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"✗ Migration error: {e}")
            raise


def downgrade():
    app = create_app()

    with app.app_context():
        try:
            for tbl in ("price_accuracy_finding", "price_accuracy_spike_ack", "price_accuracy_run"):
                drop_table_if_exists(db.session, tbl)
            db.session.commit()
            print("✓ Dropped price accuracy tables")
        except Exception as e:
            db.session.rollback()
            print(f"✗ downgrade error: {e}")
            raise


if __name__ == "__main__":
    upgrade()
    print("Migration completed.")
