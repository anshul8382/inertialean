"""
Migration script to set up roles and permissions system
Run this script to:
1. Create Role and RolePermission tables
2. Seed default roles (admin, manager, advisor)
3. Optionally convert existing users to use new roles
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from models import db, User, Role, RolePermission
from utils.permissions import get_all_routes
from datetime import datetime

def create_tables(app):
    """Create Role and RolePermission tables if they don't exist"""
    with app.app_context():
        # Create new tables
        db.create_all()
        
        # Add role_id column to user table if it doesn't exist
        try:
            from sqlalchemy import text
            with db.engine.connect() as conn:
                # Check if column exists
                result = conn.execute(text("""
                    SELECT COUNT(*) as count 
                    FROM information_schema.COLUMNS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                    AND TABLE_NAME = 'user' 
                    AND COLUMN_NAME = 'role_id'
                """))
                count = result.fetchone()[0]
                
                if count == 0:
                    # Add the column
                    conn.execute(text("""
                        ALTER TABLE `user` 
                        ADD COLUMN `role_id` INT NULL,
                        ADD CONSTRAINT `fk_user_role` 
                        FOREIGN KEY (`role_id`) REFERENCES `role` (`id`) 
                        ON DELETE SET NULL
                    """))
                    conn.commit()
                    print("  Added role_id column to user table")
                else:
                    print("  role_id column already exists in user table")
        except Exception as e:
            print(f"  Warning: Could not add role_id column: {str(e)}")
            print("  You may need to add it manually: ALTER TABLE user ADD COLUMN role_id INT NULL")
        
        print("✓ Tables created/verified")

def seed_default_roles(app):
    """Create default system roles"""
    with app.app_context():
        default_roles = [
            {
                'name': 'admin',
                'display_name': 'Administrator',
                'description': 'Full system access with all permissions',
                'is_system_role': True,
                'parent_role_id': None
            },
            {
                'name': 'manager',
                'display_name': 'Manager',
                'description': 'Manager role with elevated permissions',
                'is_system_role': True,
                'parent_role_id': None
            },
            {
                'name': 'advisor',
                'display_name': 'Advisor',
                'description': 'Standard advisor role with basic permissions',
                'is_system_role': True,
                'parent_role_id': None
            }
        ]
        
        created_count = 0
        for role_data in default_roles:
            existing = Role.query.filter_by(name=role_data['name']).first()
            if not existing:
                role = Role(**role_data)
                db.session.add(role)
                created_count += 1
                print(f"  Created role: {role_data['name']}")
            else:
                print(f"  Role already exists: {role_data['name']}")
        
        db.session.commit()
        print(f"✓ Seeded {created_count} default roles")
        return created_count > 0

def assign_default_permissions(app):
    """Assign default permissions to system roles"""
    with app.app_context():
        admin_role = Role.query.filter_by(name='admin').first()
        manager_role = Role.query.filter_by(name='manager').first()
        advisor_role = Role.query.filter_by(name='advisor').first()
        
        if not admin_role or not manager_role or not advisor_role:
            print("✗ Error: Default roles not found. Please run seed_default_roles first.")
            return
        
        all_routes = get_all_routes()
        all_endpoints = []
        for category, routes in all_routes.items():
            for endpoint, _ in routes:
                all_endpoints.append(endpoint)
        
        # Admin gets all permissions
        admin_perms = 0
        for endpoint in all_endpoints:
            existing = RolePermission.query.filter_by(
                role_id=admin_role.id,
                route_endpoint=endpoint
            ).first()
            if not existing:
                perm = RolePermission(
                    role_id=admin_role.id,
                    route_endpoint=endpoint,
                    has_access=True
                )
                db.session.add(perm)
                admin_perms += 1
        
        # Manager gets most permissions (exclude some admin-only features)
        manager_excluded = [
            'users.list_users',
            'users.add_user',
            'users.edit_user',
            'users.delete_user',
            'roles.list_roles',
            'roles.add_role',
            'roles.edit_role',
            'roles.delete_role',
            'roles.edit_role_permissions',
        ]
        manager_perms = 0
        for endpoint in all_endpoints:
            if endpoint not in manager_excluded:
                existing = RolePermission.query.filter_by(
                    role_id=manager_role.id,
                    route_endpoint=endpoint
                ).first()
                if not existing:
                    perm = RolePermission(
                        role_id=manager_role.id,
                        route_endpoint=endpoint,
                        has_access=True
                    )
                    db.session.add(perm)
                    manager_perms += 1
        
        # Advisor gets basic permissions
        advisor_allowed = [
            'clients.list_clients',
            'clients.view_client',
            'clients.list_review_schedules',
            'main.list_monthly_investments',
            'main.transactions',
            'main.list_cashflows',
            'enhanced_review.period_analysis_v2',
            'unified_recommendations.unified_recommendations',
            'workflows.list_workflows',
            'leads.list_leads',
            'meetings.list_meetings',
            'tickets.list_tickets',
        ]
        advisor_perms = 0
        for endpoint in advisor_allowed:
            existing = RolePermission.query.filter_by(
                role_id=advisor_role.id,
                route_endpoint=endpoint
            ).first()
            if not existing:
                perm = RolePermission(
                    role_id=advisor_role.id,
                    route_endpoint=endpoint,
                    has_access=True
                )
                db.session.add(perm)
                advisor_perms += 1
        
        db.session.commit()
        print(f"✓ Assigned permissions: Admin ({admin_perms}), Manager ({manager_perms}), Advisor ({advisor_perms})")

def convert_existing_users(app, dry_run=True):
    """Convert existing users to use new role system based on their current role"""
    with app.app_context():
        users = User.query.all()
        converted = 0
        
        print(f"\n{'[DRY RUN] ' if dry_run else ''}Converting existing users:")
        
        for user in users:
            # Skip if user already has a role_id
            if user.role_id:
                continue
            
            # Map old role string to new role
            role_mapping = {
                'admin': 'admin',
                'manager': 'manager',
                'advisor': 'advisor'
            }
            
            old_role = user.role.lower() if user.role else 'advisor'
            new_role_name = role_mapping.get(old_role, 'advisor')
            
            new_role = Role.query.filter_by(name=new_role_name).first()
            if new_role:
                if not dry_run:
                    user.role_id = new_role.id
                    db.session.commit()
                print(f"  {user.username}: {old_role} -> {new_role_name}")
                converted += 1
        
        if not dry_run:
            db.session.commit()
            print(f"✓ Converted {converted} users")
        else:
            print(f"  [DRY RUN] Would convert {converted} users")
        
        return converted

def main():
    """Main migration function"""
    from main import create_app
    
    app = create_app()
    
    print("=" * 60)
    print("Roles and Permissions Migration Script")
    print("=" * 60)
    
    try:
        # Step 1: Create tables
        print("\n1. Creating tables...")
        create_tables(app)
        
        # Step 2: Seed default roles
        print("\n2. Seeding default roles...")
        seed_default_roles(app)
        
        # Step 3: Assign default permissions
        print("\n3. Assigning default permissions...")
        assign_default_permissions(app)
        
        # Step 4: Convert existing users (dry run first)
        print("\n4. Converting existing users...")
        convert_existing_users(app, dry_run=True)
        
        print("\n" + "=" * 60)
        print("Migration completed successfully!")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Review the dry run output above")
        print("2. To actually convert users, run: convert_existing_users(app, dry_run=False)")
        print("3. Access /roles to manage roles and permissions")
        print("4. Edit users to assign custom roles")
        
    except Exception as e:
        print(f"\n✗ Error during migration: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()

