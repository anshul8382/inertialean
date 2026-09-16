#!/bin/bash

# Script to check the status of production and development environments
echo "=== Inertia Investment App Environment Status ==="
echo ""

# Check production environment
echo "📊 PRODUCTION ENVIRONMENT:"
echo "   Directory: /home/inertia/app"
echo "   Database: inertia_app2025"
echo "   Git Branch: $(cd /home/inertia/app && git branch --show-current 2>/dev/null || echo 'Unknown')"
echo "   Database Tables: $(mysql -u inertia_admin -p'!Nert!a2025$' -e "USE inertia_app2025; SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'inertia_app2025';" 2>/dev/null | tail -1)"
echo ""

# Check development environment
echo "🔧 DEVELOPMENT ENVIRONMENT:"
echo "   Directory: /home/inertia/app_dev"
echo "   Database: inertia_app2025_dev"
echo "   Git Branch: $(cd /home/inertia/app_dev && git branch --show-current 2>/dev/null || echo 'Unknown')"
echo "   Database Tables: $(mysql -u inertia_admin -p'!Nert!a2025$' -e "USE inertia_app2025_dev; SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'inertia_app2025_dev';" 2>/dev/null | tail -1)"
echo ""

# Check test environment
echo "🧪 TEST ENVIRONMENT:"
echo "   Directory: /home/inertia/app_test"
echo "   Database: inertia_app2025_test"
echo "   Git Branch: $(cd /home/inertia/app_test && git branch --show-current 2>/dev/null || echo 'Unknown')"
echo "   Database Tables: $(mysql -u inertia_admin -p'!Nert!a2025$' -e "USE inertia_app2025_test; SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'inertia_app2025_test';" 2>/dev/null | tail -1 2>/dev/null || echo 'Not set up')"
echo ""

# Check if processes are running
echo "🚀 RUNNING PROCESSES:"
echo "   Production: $(ps aux | grep -v grep | grep -c 'gunicorn.*main:app' || echo '0') gunicorn processes"
echo "   Development: $(ps aux | grep -v grep | grep -c 'python3.*run_app.py' || echo '0') Flask processes"
echo "   Test: $(ps aux | grep -v grep | grep -c 'flask run.*--port 5002' || echo '0') Flask test processes"
echo ""

# Show startup commands
echo "🎯 STARTUP COMMANDS:"
echo "   Start Production: cd /home/inertia/app && ./start_production.sh (Port: 5000)"
echo "   Start Development: cd /home/inertia/app_dev && ./start_development.sh (Port: 5001)"
echo "   Start Test: cd /home/inertia/app_test && ./start_test.sh (Port: 5002)"
echo "   Airflow: http://your-domain:8080"
echo ""

# Check database connectivity
echo "🔗 DATABASE CONNECTIVITY:"
echo "   Production DB: $(mysql -u inertia_admin -p'!Nert!a2025$' -e "SELECT 'OK' as status;" 2>/dev/null | tail -1 || echo 'ERROR')"
echo "   Development DB: $(mysql -u inertia_admin -p'!Nert!a2025$' -e "USE inertia_app2025_dev; SELECT 'OK' as status;" 2>/dev/null | tail -1 || echo 'ERROR')"
echo "   Test DB: $(mysql -u inertia_admin -p'!Nert!a2025$' -e "USE inertia_app2025_test; SELECT 'OK' as status;" 2>/dev/null | tail -1 || echo 'NOT SET UP')"
echo ""

echo "✅ Environment check completed!"
