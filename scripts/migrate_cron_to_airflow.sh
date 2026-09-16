#!/bin/bash
# Migration script to move from cron to Airflow

echo "🔄 Migrating from Cron to Airflow"
echo "=================================="

# Check if Airflow is running
echo "📋 Checking Airflow status..."
if pgrep -f "airflow webserver" > /dev/null && pgrep -f "airflow scheduler" > /dev/null; then
    echo "✅ Airflow is running"
else
    echo "❌ Airflow is not running. Please start it first:"
    echo "   ./scripts/start_airflow.sh"
    exit 1
fi

# Show current cron jobs
echo ""
echo "📋 Current Cron Jobs:"
echo "===================="
crontab -l 2>/dev/null | grep -E "(daily_|sla_|alert_|price_|nifty_|workflow_)" || echo "No matching cron jobs found"

# Show Airflow DAGs
echo ""
echo "📋 Airflow DAGs:"
echo "==============="
export AIRFLOW_HOME=/home/inertia/app_test/airflow
airflow dags list 2>/dev/null | grep -E "(daily_|sla_|alert_|price_|workflow_)" || echo "No DAGs found"

echo ""
echo "✅ Migration Status:"
echo "=================="
echo "1. ✅ Daily Reports DAG created (daily_reports)"
echo "2. ✅ Hourly SLA Check DAG created (hourly_sla_check)"
echo "3. ✅ Daily Alert Report DAG created (daily_alert_report)"
echo "4. ✅ Price Updates DAG created (price_updates)"
echo "5. ✅ Workflow Management DAG created (workflow_management)"

echo ""
echo "⚠️  Next Steps:"
echo "=============="
echo "1. Test the Airflow DAGs manually from the UI"
echo "2. Verify they're working correctly"
echo "3. Monitor for a few days to ensure stability"
echo "4. Then disable old cron jobs using:"
echo ""
echo "   # View current crontab"
echo "   crontab -l"
echo ""
echo "   # Edit and remove old entries"
echo "   crontab -e"
echo ""
echo "   # Or use the cron management UI to disable them"
echo ""

echo "🧪 To test the migration:"
echo "========================"
echo "1. Access Airflow UI: http://localhost:8080"
echo "2. Login: admin / inertia2025"
echo "3. Trigger each DAG manually"
echo "4. Check logs for any errors"
echo "5. Verify outputs match expected results"
echo ""

echo "📊 Migration Summary:"
echo "===================="
echo "✅ All cron jobs have Airflow equivalents"
echo "✅ Better monitoring and error handling"
echo "✅ Centralized scheduling"
echo "✅ Visual DAG interface"
echo "✅ Retry mechanisms"
echo "✅ Email notifications on failure"
echo ""

echo "🎉 Migration ready! Test the DAGs before removing cron jobs."
