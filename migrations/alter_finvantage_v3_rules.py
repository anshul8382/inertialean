#!/usr/bin/env python3
"""
Migration: Add rules, products, insurance questionnaire tables.
Run: python migrations/alter_finvantage_v3_rules.py
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text


def upgrade():
    app = create_app()
    with app.app_context():
        engine = db.get_engine(bind_key='finvantage')
        stmts = [
            """
            CREATE TABLE IF NOT EXISTS finvantage_rule (
                id INT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(50) NOT NULL UNIQUE,
                name VARCHAR(200) NOT NULL,
                description TEXT,
                category VARCHAR(50),
                priority INT DEFAULT 0,
                is_active TINYINT(1) DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS finvantage_product (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                product_type VARCHAR(50) NOT NULL,
                provider VARCHAR(200),
                description TEXT,
                min_coverage DECIMAL(15,2),
                max_coverage DECIMAL(15,2),
                metadata_json JSON,
                is_active TINYINT(1) DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS finvantage_rule_product (
                id INT AUTO_INCREMENT PRIMARY KEY,
                rule_id INT NOT NULL,
                product_id INT NOT NULL,
                CONSTRAINT fk_rp_rule FOREIGN KEY (rule_id) REFERENCES finvantage_rule(id) ON DELETE CASCADE,
                CONSTRAINT fk_rp_product FOREIGN KEY (product_id) REFERENCES finvantage_product(id) ON DELETE CASCADE,
                CONSTRAINT uq_rule_product UNIQUE (rule_id, product_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS finvantage_insurance_question (
                id INT AUTO_INCREMENT PRIMARY KEY,
                text TEXT NOT NULL,
                question_type VARCHAR(30) NOT NULL,
                options JSON,
                category VARCHAR(50),
                order_idx INT DEFAULT 0,
                rule_code VARCHAR(50),
                is_active TINYINT(1) DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS finvantage_user_insurance_assessment (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                answers JSON,
                suggested_product_ids JSON,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_ua_user FOREIGN KEY (user_id) REFERENCES finvantage_user(id) ON DELETE CASCADE,
                CONSTRAINT uq_user_insurance UNIQUE (user_id)
            )
            """,
        ]
        for stmt in stmts:
            try:
                with engine.connect() as conn:
                    conn.execute(text(stmt))
                    conn.commit()
                print("✓ Created table")
            except Exception as e:
                if "1050" in str(e) or "already exists" in str(e).lower():
                    print("  (table exists, skip)")
                else:
                    raise
        print("\n✅ FinVantage v3 rules migration complete!")


if __name__ == '__main__':
    upgrade()
