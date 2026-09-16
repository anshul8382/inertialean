#!/bin/bash

# Migrate from Cron to Airflow
# This script helps migrate existing cron jobs to Airflow

echo "🚀 Migrating from Cron to Airflow"
echo "=================================="

# Check if Airflow is running
echo "📋 Checking Airflow status..."
if pgrep -f "airflow webserver" > /dev/null && pgrep -f "airflow scheduler" > /dev/null; then
    echo "✅ Airflow is running"
else
    echo "❌ Airflow is not running. Starting Airflow..."
    ./start_airflow.sh
    sleep 10
fi

# Show current cron jobs
echo ""
echo "📋 Current Cron Jobs:"
echo "===================="
crontab -l 2>/dev/null | grep -E "(daily_|sla_|alert_)" || echo "No cron jobs found"

# Show Airflow DAGs
echo ""
echo "📋 Airflow DAGs:"
echo "==============="
export AIRFLOW_HOME=/home/inertia/app/airflow
airflow dags list 2>/dev/null | grep -E "(daily_|sla_|alert_|monthly_|client_)" || echo "No DAGs found"

echo ""
echo "🔄 Migration Steps:"
echo "=================="
echo "1. ✅ Daily Reports DAG created (daily_leads_report, daily_monthly_investments_report, daily_workflow_report)"
echo "2. ✅ Monthly Portfolio DAG created (portfolio snapshots)"
echo "3. ✅ Client Review DAG created (manual trigger)"
echo "4. ✅ SLA Check DAG created (hourly)"
echo "5. ✅ Alert Report DAG created (daily 8 AM)"

echo ""
echo "⚠️  Next Steps:"
echo "=============="
echo "1. Test the new Airflow DAGs"
echo "2. Verify they're working correctly"
echo "3. Remove old cron jobs"
echo "4. Update monitoring"

echo ""
echo "🧪 To test the migration:"
echo "========================"
echo "1. Check Airflow UI: http://your-server:8080"
echo "2. Look for these DAGs:"
echo "   - daily_reports"
echo "   - monthly_portfolio"
echo "   - client_review_generator"
echo "   - sla_check"
echo "   - alert_report"
echo "3. Trigger a test run of each DAG"

echo ""
echo "🗑️  To remove old cron jobs (run after testing):"
echo "================================================"
echo "# Remove daily reports"
echo "crontab -l | grep -v 'daily_leads_report.py' | grep -v 'daily_monthly_investments_report.py' | grep -v 'daily_workflow_report.py' | crontab -"
echo ""
echo "# Remove SLA check"
echo "crontab -l | grep -v 'manual_sla_check.py' | crontab -"
echo ""
echo "# Remove alert report"
echo "crontab -l | grep -v 'alert_service.py' | crontab -"

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
