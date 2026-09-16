#!/usr/bin/env python3
"""
Migration: Add Account Management tables.
Creates: account_head, bank_statement, bank_statement_transaction.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text
from utils.sql_ddl import drop_table_if_exists


def _table_exists(inspector, name):
    return name in inspector.get_table_names()


def upgrade():
    """Create Account Management tables."""
    app = create_app()
    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            created = []

            if not _table_exists(inspector, 'account_head'):
                db.session.execute(text("""
                    CREATE TABLE account_head (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(100) NOT NULL,
                        head_type VARCHAR(20) NOT NULL,
                        description TEXT,
                        sort_order INT DEFAULT 0,
                        is_active TINYINT(1) DEFAULT 1,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        created_by INT NOT NULL,
                        FOREIGN KEY (created_by) REFERENCES user(id),
                        UNIQUE KEY uk_account_head_name_type (name, head_type),
                        INDEX ix_account_head_type (head_type)
                    )
                """))
                created.append('account_head')
                print("✓ Created account_head")

            if not _table_exists(inspector, 'bank_statement'):
                db.session.execute(text("""
                    CREATE TABLE bank_statement (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        account_name VARCHAR(200),
                        upload_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                        created_by INT NOT NULL,
                        notes TEXT,
                        FOREIGN KEY (created_by) REFERENCES user(id),
                        INDEX ix_bank_statement_created_by (created_by)
                    )
                """))
                created.append('bank_statement')
                print("✓ Created bank_statement")

            if not _table_exists(inspector, 'bank_statement_transaction'):
                db.session.execute(text("""
                    CREATE TABLE bank_statement_transaction (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        bank_statement_id INT NOT NULL,
                        transaction_date DATE NOT NULL,
                        value_date DATE,
                        reference_no VARCHAR(200),
                        description TEXT,
                        description_sanitized TEXT,
                        withdrawal_amount DECIMAL(15,2) DEFAULT 0.00,
                        deposit_amount DECIMAL(15,2) DEFAULT 0.00,
                        running_balance DECIMAL(15,2),
                        income_expense_head_id INT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (bank_statement_id) REFERENCES bank_statement(id) ON DELETE CASCADE,
                        FOREIGN KEY (income_expense_head_id) REFERENCES account_head(id),
                        INDEX ix_bank_stmt_txn_statement (bank_statement_id),
                        INDEX ix_bank_stmt_txn_head (income_expense_head_id),
                        INDEX ix_bank_stmt_txn_date (transaction_date)
                    )
                """))
                created.append('bank_statement_transaction')
                print("✓ Created bank_statement_transaction")

            if not created:
                print("→ All Account Management tables already exist")
            db.session.commit()
            print("\n✅ Migration completed successfully!")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Migration failed: {str(e)}")
            import traceback
            traceback.print_exc()
            return False


def downgrade():
    """Drop Account Management tables in reverse dependency order."""
    app = create_app()
    with app.app_context():
        try:
            tables = ['bank_statement_transaction', 'bank_statement', 'account_head']
            for name in tables:
                drop_table_if_exists(db.session, name)
                print(f"✓ Dropped {name}")
            db.session.commit()
            print("\n✅ Rollback completed successfully!")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Rollback failed: {str(e)}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'downgrade':
        print("Rolling back Account Management tables...")
        downgrade()
    else:
        print("Running Account Management tables migration...")
        upgrade()
