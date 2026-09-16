#!/bin/bash
# Reset Airflow admin password

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
AIRFLOW_HOME="$APP_DIR/airflow"

export AIRFLOW_HOME="$AIRFLOW_HOME"

echo "🔑 Resetting Airflow Admin Password"
echo "=================================="

# Stop Airflow if running
echo "🛑 Stopping Airflow..."
pkill -f "airflow standalone" 2>/dev/null
pkill -f "airflow webserver" 2>/dev/null
pkill -f "airflow scheduler" 2>/dev/null
sleep 2

# For Airflow 3.x, we need to use the API or reset via database
# The easiest way is to delete and recreate the admin user

NEW_PASSWORD="${1:-inertia2025}"

echo ""
echo "📝 Setting password to: $NEW_PASSWORD"
echo ""

# Airflow 3.x doesn't have 'users' CLI command, so we'll use Python
cd "$APP_DIR"
python3 << EOF
import os
import sys
sys.path.insert(0, '.')

# Set Airflow home
os.environ['AIRFLOW_HOME'] = '$AIRFLOW_HOME'

# Import Airflow
from airflow.auth.managers.fab.models import User
from airflow.auth.managers.fab.security_manager import FabSecurityManager
from airflow import settings
from airflow.utils.db import create_session

# Initialize Airflow
settings.initialize()

# Create or update admin user
with create_session() as session:
    admin_user = session.query(User).filter_by(username='admin').first()
    
    if admin_user:
        # Update existing user
        from airflow.auth.managers.fab.security_manager import hash_password
        admin_user.password = hash_password('$NEW_PASSWORD')
        admin_user.active = True
        admin_user.superuser = True
        session.commit()
        print("✅ Updated existing admin user password")
    else:
        # Create new admin user
        from airflow.auth.managers.fab.security_manager import hash_password
        admin_user = User(
            username='admin',
            email='anshul@equities4wealth.com',
            first_name='Admin',
            last_name='User',
            password=hash_password('$NEW_PASSWORD'),
            active=True,
            superuser=True
        )
        session.add(admin_user)
        session.commit()
        print("✅ Created new admin user")

print("")
print("🔑 Login Credentials:")
print("   Username: admin")
print("   Password: $NEW_PASSWORD")
EOF

echo ""
echo "✅ Password reset complete!"
echo ""
echo "🚀 Restart Airflow:"
echo "   ./scripts/start_airflow.sh"
echo ""
