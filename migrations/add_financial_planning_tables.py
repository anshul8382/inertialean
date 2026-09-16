#!/usr/bin/env python3
"""
Migration: Add Financial Planning tables.
Creates: expense_category, personal_cashflow_category, financial_goal, financial_plan,
         budget, personal_cashflow, goal_funding_tag, financial_scenario_run.
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
    """Create all Financial Planning tables in dependency order."""
    app = create_app()
    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            created = []

            if not _table_exists(inspector, 'expense_category'):
                db.session.execute(text("""
                    CREATE TABLE expense_category (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(100) NOT NULL UNIQUE,
                        description TEXT,
                        is_active TINYINT(1) DEFAULT 1,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                created.append('expense_category')
                print("✓ Created expense_category")

            if not _table_exists(inspector, 'personal_cashflow_category'):
                db.session.execute(text("""
                    CREATE TABLE personal_cashflow_category (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(100) NOT NULL UNIQUE,
                        description TEXT,
                        is_active TINYINT(1) DEFAULT 1,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                created.append('personal_cashflow_category')
                print("✓ Created personal_cashflow_category")

            if not _table_exists(inspector, 'financial_goal'):
                db.session.execute(text("""
                    CREATE TABLE financial_goal (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        client_id INT NOT NULL,
                        goal_name VARCHAR(200) NOT NULL,
                        goal_type VARCHAR(50) NOT NULL,
                        target_amount DECIMAL(15,2) NOT NULL,
                        target_date DATE NOT NULL,
                        current_progress DECIMAL(15,2) DEFAULT 0.00,
                        monthly_contribution DECIMAL(15,2),
                        expected_return_rate DECIMAL(5,2) DEFAULT 12.00,
                        inflation_rate DECIMAL(5,2) DEFAULT 0.00,
                        priority INT DEFAULT 1,
                        status VARCHAR(20) DEFAULT 'active',
                        notes TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        created_by INT NOT NULL,
                        FOREIGN KEY (client_id) REFERENCES client(id),
                        FOREIGN KEY (created_by) REFERENCES user(id)
                    )
                """))
                created.append('financial_goal')
                print("✓ Created financial_goal")

            if not _table_exists(inspector, 'financial_plan'):
                db.session.execute(text("""
                    CREATE TABLE financial_plan (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        client_id INT NOT NULL UNIQUE,
                        plan_name VARCHAR(200) NOT NULL DEFAULT 'Financial Plan',
                        plan_date DATE NOT NULL,
                        review_date DATE,
                        current_net_worth DECIMAL(15,2) DEFAULT 0.00,
                        cash_and_bank DECIMAL(15,2) DEFAULT 0.00,
                        real_estate_value DECIMAL(15,2) DEFAULT 0.00,
                        other_assets_value DECIMAL(15,2) DEFAULT 0.00,
                        other_liabilities DECIMAL(15,2) DEFAULT 0.00,
                        include_inertia_portfolio TINYINT(1) DEFAULT 1,
                        include_cash_and_bank TINYINT(1) DEFAULT 1,
                        include_real_estate TINYINT(1) DEFAULT 0,
                        include_other_assets TINYINT(1) DEFAULT 0,
                        include_liabilities TINYINT(1) DEFAULT 1,
                        monthly_income DECIMAL(15,2),
                        monthly_expenses DECIMAL(15,2),
                        monthly_savings DECIMAL(15,2),
                        emergency_fund_target DECIMAL(15,2),
                        emergency_fund_current DECIMAL(15,2),
                        risk_tolerance VARCHAR(20),
                        time_horizon INT,
                        life_insurance_coverage DECIMAL(15,2),
                        health_insurance_coverage DECIMAL(15,2),
                        total_liabilities DECIMAL(15,2),
                        plan_details JSON,
                        status VARCHAR(20) DEFAULT 'draft',
                        notes TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        created_by INT NOT NULL,
                        FOREIGN KEY (client_id) REFERENCES client(id),
                        FOREIGN KEY (created_by) REFERENCES user(id)
                    )
                """))
                created.append('financial_plan')
                print("✓ Created financial_plan")

            if not _table_exists(inspector, 'budget'):
                db.session.execute(text("""
                    CREATE TABLE budget (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        client_id INT NOT NULL,
                        financial_plan_id INT,
                        budget_type VARCHAR(20) NOT NULL,
                        year INT NOT NULL,
                        month INT,
                        total_budget DECIMAL(15,2) NOT NULL,
                        actual_spending DECIMAL(15,2) DEFAULT 0.00,
                        category_budgets JSON,
                        category_actuals JSON,
                        status VARCHAR(20) DEFAULT 'active',
                        notes TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        created_by INT NOT NULL,
                        FOREIGN KEY (client_id) REFERENCES client(id),
                        FOREIGN KEY (financial_plan_id) REFERENCES financial_plan(id),
                        FOREIGN KEY (created_by) REFERENCES user(id)
                    )
                """))
                created.append('budget')
                print("✓ Created budget")

            if not _table_exists(inspector, 'personal_cashflow'):
                db.session.execute(text("""
                    CREATE TABLE personal_cashflow (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        client_id INT NOT NULL,
                        category_id INT,
                        cashflow_type VARCHAR(20) NOT NULL,
                        amount DECIMAL(15,2) NOT NULL,
                        frequency VARCHAR(20) NOT NULL DEFAULT 'MONTHLY',
                        start_date DATE NOT NULL,
                        end_date DATE,
                        day_of_month INT,
                        is_active TINYINT(1) DEFAULT 1,
                        notes TEXT,
                        created_by INT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (client_id) REFERENCES client(id),
                        FOREIGN KEY (category_id) REFERENCES personal_cashflow_category(id),
                        FOREIGN KEY (created_by) REFERENCES user(id),
                        INDEX ix_personal_cashflow_client_id (client_id)
                    )
                """))
                created.append('personal_cashflow')
                print("✓ Created personal_cashflow")

            if not _table_exists(inspector, 'goal_funding_tag'):
                db.session.execute(text("""
                    CREATE TABLE goal_funding_tag (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        goal_id INT NOT NULL,
                        client_id INT NOT NULL,
                        portfolio_id INT,
                        security_id INT,
                        allocation_percent DECIMAL(5,2),
                        notes TEXT,
                        created_by INT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (goal_id) REFERENCES financial_goal(id),
                        FOREIGN KEY (client_id) REFERENCES client(id),
                        FOREIGN KEY (portfolio_id) REFERENCES portfolio(id),
                        FOREIGN KEY (security_id) REFERENCES security(id),
                        FOREIGN KEY (created_by) REFERENCES user(id),
                        INDEX ix_goal_funding_tag_goal_id (goal_id),
                        INDEX ix_goal_funding_tag_client_id (client_id)
                    )
                """))
                created.append('goal_funding_tag')
                print("✓ Created goal_funding_tag")

            if not _table_exists(inspector, 'financial_scenario_run'):
                db.session.execute(text("""
                    CREATE TABLE financial_scenario_run (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        client_id INT NOT NULL,
                        name VARCHAR(200) NOT NULL DEFAULT 'Scenario Run',
                        assumptions JSON,
                        results JSON,
                        created_by INT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (client_id) REFERENCES client(id),
                        FOREIGN KEY (created_by) REFERENCES user(id),
                        INDEX ix_financial_scenario_run_client_id (client_id)
                    )
                """))
                created.append('financial_scenario_run')
                print("✓ Created financial_scenario_run")

            if not created:
                print("→ All Financial Planning tables already exist")
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
    """Drop Financial Planning tables in reverse dependency order."""
    app = create_app()
    with app.app_context():
        try:
            tables = [
                'financial_scenario_run',
                'goal_funding_tag',
                'personal_cashflow',
                'budget',
                'financial_plan',
                'financial_goal',
                'personal_cashflow_category',
                'expense_category',
            ]
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
        print("Rolling back Financial Planning tables...")
        downgrade()
    else:
        print("Running Financial Planning tables migration...")
        upgrade()
