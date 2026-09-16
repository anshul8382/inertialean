#!/usr/bin/env python3
"""
Setup Workflow Migration
Interactive script to set up workflow migration from test to production database
"""

import sys
import os
from datetime import datetime, timedelta

# Add the application directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from main import create_app
from models import MonthlyInvestment, Workflow, User, Client, db

def get_database_config():
    """Get database configuration interactively"""
    print("🔧 Database Configuration Setup")
    print("=" * 50)
    
    config = {}
    config['test_host'] = input("Test database host [localhost]: ").strip() or "localhost"
    config['test_user'] = input("Test database user [root]: ").strip() or "root"
    config['test_password'] = input("Test database password: ").strip()
    config['test_database'] = input("Test database name [inertia_test]: ").strip() or "inertia_test"
    
    config['prod_host'] = input("Production database host [localhost]: ").strip() or "localhost"
    config['prod_user'] = input("Production database user [root]: ").strip() or "root"
    config['prod_password'] = input("Production database password: ").strip()
    config['prod_database'] = input("Production database name [inertia_app2025]: ").strip() or "inertia_app2025"
    
    return config

def create_migration_script(config):
    """Create the actual migration script with the provided configuration"""
    
    script_content = f'''#!/usr/bin/env python3
"""
Workflow Migration Script - Generated
Copies workflow data from test database to production database
"""

import sys
import os
from datetime import datetime, timedelta
import pymysql

# Add the application directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from main import create_app
from models import MonthlyInvestment, Workflow, User, Client, db

# Database connection configurations
TEST_DB_CONFIG = {{
    'host': '{config['test_host']}',
    'user': '{config['test_user']}',
    'password': '{config['test_password']}',
    'database': '{config['test_database']}',
    'charset': 'utf8mb4'
}}

PROD_DB_CONFIG = {{
    'host': '{config['prod_host']}',
    'user': '{config['prod_user']}',
    'password': '{config['prod_password']}',
    'database': '{config['prod_database']}',
    'charset': 'utf8mb4'
}}

def connect_to_database(config):
    """Connect to database using pymysql"""
    try:
        connection = pymysql.connect(**config)
        return connection
    except Exception as e:
        print(f"❌ Failed to connect to database: {{e}}")
        return None

def get_test_workflows(test_conn):
    """Get workflows from test database"""
    try:
        cursor = test_conn.cursor(pymysql.cursors.DictCursor)
        
        query = """
        SELECT 
            w.id as workflow_id,
            w.monthly_investment_id,
            w.current_stage,
            w.planned_amount,
            w.actual_amount,
            w.investment_date,
            w.target_completion_date,
            w.actual_completion_date,
            w.notes,
            w.created_at,
            w.created_by,
            w.updated_at,
            w.is_archived,
            w.archived_at,
            w.archived_reason,
            mi.client_id,
            c.name as client_name
        FROM workflow w
        JOIN monthly_investment mi ON w.monthly_investment_id = mi.id
        JOIN client c ON mi.client_id = c.id
        ORDER BY w.created_at DESC
        """
        
        cursor.execute(query)
        workflows = cursor.fetchall()
        cursor.close()
        
        return workflows
        
    except Exception as e:
        print(f"❌ Error fetching test workflows: {{e}}")
        return []

def copy_workflows_for_client(test_workflows, client_name):
    """Copy workflows for a specific client"""
    try:
        app = create_app()
        
        with app.app_context():
            # Find client in production database
            client = Client.query.filter(Client.name.ilike(f'%{{client_name}}%')).first()
            
            if not client:
                print(f"⚠️ No matching client found for '{{client_name}}' in production database")
                return 0
            
            print(f"📋 Found matching client: {{client.name}} (ID: {{client.id}})")
            
            # Filter workflows for this client
            client_workflows = [w for w in test_workflows if w['client_name'] == client_name]
            
            if not client_workflows:
                print(f"ℹ️ No workflows found for client '{{client_name}}' in test database")
                return 0
            
            print(f"📊 Found {{len(client_workflows)}} workflows for client '{{client_name}}'")
            
            # Get admin user from production
            admin_user = User.query.get(1)
            if not admin_user:
                print("❌ Admin user not found in production database")
                return 0
            
            copied_count = 0
            
            for test_workflow in client_workflows:
                try:
                    # Check if similar workflow already exists
                    existing_workflow = Workflow.query.join(MonthlyInvestment).filter(
                        MonthlyInvestment.client_id == client.id,
                        Workflow.current_stage == test_workflow['current_stage'],
                        Workflow.planned_amount == test_workflow['planned_amount'],
                        Workflow.investment_date == test_workflow['investment_date']
                    ).first()
                    
                    if existing_workflow:
                        print(f"⚠️ Similar workflow already exists for client {{client.name}}, skipping")
                        continue
                    
                    # Create new monthly investment
                    monthly_investment = MonthlyInvestment(
                        client_id=client.id,
                        portfolio_id=1,  # Default portfolio
                        planned_amount=test_workflow['planned_amount'],
                        investment_date=test_workflow['investment_date'],
                        status='PENDING',
                        created_by=admin_user.id
                    )
                    db.session.add(monthly_investment)
                    db.session.flush()
                    
                    # Create workflow
                    workflow = Workflow(
                        monthly_investment_id=monthly_investment.id,
                        current_stage=test_workflow['current_stage'],
                        planned_amount=test_workflow['planned_amount'],
                        actual_amount=test_workflow['actual_amount'],
                        investment_date=test_workflow['investment_date'],
                        target_completion_date=test_workflow['target_completion_date'],
                        actual_completion_date=test_workflow['actual_completion_date'],
                        notes=test_workflow['notes'] or f"Migrated from test database - Original ID: {{test_workflow['workflow_id']}}",
                        created_by=admin_user.id,
                        is_archived=test_workflow['is_archived'] or False,
                        archived_at=test_workflow['archived_at'],
                        archived_reason=test_workflow['archived_reason']
                    )
                    
                    db.session.add(workflow)
                    db.session.flush()
                    
                    # Update monthly investment with workflow ID
                    monthly_investment.workflow_id = workflow.id
                    
                    copied_count += 1
                    print(f"✅ Copied workflow for {{client.name}} - Stage: {{test_workflow['current_stage']}}, Amount: {{test_workflow['planned_amount']}}")
                    
                except Exception as e:
                    print(f"❌ Failed to copy workflow for {{client_name}}: {{str(e)}}")
                    continue
            
            # Commit all changes for this client
            db.session.commit()
            print(f"🎉 Successfully copied {{copied_count}} workflows for client '{{client_name}}'")
            
            return copied_count
            
    except Exception as e:
        print(f"❌ Error copying workflows for client {{client_name}}: {{str(e)}}")
        db.session.rollback()
        return 0

def main():
    """Main function"""
    print("🔄 Starting workflow migration from test database...")
    print("=" * 70)
    
    # Connect to test database
    print("📡 Connecting to test database...")
    test_conn = connect_to_database(TEST_DB_CONFIG)
    if not test_conn:
        print("❌ Failed to connect to test database")
        return 1
    
    try:
        # Get workflows from test database
        print("📊 Fetching workflows from test database...")
        test_workflows = get_test_workflows(test_conn)
        
        if not test_workflows:
            print("ℹ️ No workflows found in test database")
            return 0
        
        print(f"📋 Found {{len(test_workflows)}} workflows in test database")
        
        # Get unique client names from test workflows
        client_names = list(set([w['client_name'] for w in test_workflows]))
        print(f"👥 Found {{len(client_names)}} unique clients in test database")
        
        # Show clients and let user choose
        print("\\n📋 Available clients in test database:")
        for i, client_name in enumerate(client_names, 1):
            print(f"  {{i}}. {{client_name}}")
        
        print("\\nOptions:")
        print("  - Enter client numbers (e.g., 1,3,5) to copy specific clients")
        print("  - Enter 'all' to copy all clients")
        print("  - Enter 'skip' to exit")
        
        choice = input("\\nYour choice: ").strip().lower()
        
        if choice == 'skip':
            print("👋 Migration cancelled")
            return 0
        
        clients_to_process = []
        if choice == 'all':
            clients_to_process = client_names
        else:
            try:
                indices = [int(x.strip()) - 1 for x in choice.split(',')]
                clients_to_process = [client_names[i] for i in indices if 0 <= i < len(client_names)]
            except (ValueError, IndexError):
                print("❌ Invalid choice")
                return 1
        
        total_copied = 0
        
        # Copy workflows for selected clients
        for client_name in clients_to_process:
            print(f"\\n📋 Processing client: {{client_name}}")
            print("-" * 50)
            copied = copy_workflows_for_client(test_workflows, client_name)
            total_copied += copied
        
        print(f"\\n" + "=" * 70)
        print(f"🎉 Migration completed!")
        print(f"📊 Total workflows copied: {{total_copied}}")
        print(f"👥 Clients processed: {{len(clients_to_process)}}")
        
    except Exception as e:
        print(f"❌ Migration failed: {{str(e)}}")
        return 1
    
    finally:
        test_conn.close()
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''
    
    return script_content

def main():
    """Main function"""
    print("🔧 Workflow Migration Setup")
    print("=" * 50)
    print("This script will help you set up workflow migration from test to production database.")
    print()
    
    # Get database configuration
    config = get_database_config()
    
    print("\\n📝 Configuration Summary:")
    print(f"  Test DB: {config['test_user']}@{config['test_host']}/{config['test_database']}")
    print(f"  Prod DB: {config['prod_user']}@{config['prod_host']}/{config['prod_database']}")
    
    confirm = input("\\nProceed with this configuration? (y/N): ").strip().lower()
    if confirm != 'y':
        print("👋 Setup cancelled")
        return 0
    
    # Create migration script
    print("\\n📝 Creating migration script...")
    script_content = create_migration_script(config)
    
    script_filename = "run_workflow_migration.py"
    with open(script_filename, 'w') as f:
        f.write(script_content)
    
    print(f"✅ Migration script created: {script_filename}")
    print("\\n🚀 To run the migration:")
    print(f"   python3 {script_filename}")
    print("\\n⚠️  Make sure to backup your production database before running the migration!")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

