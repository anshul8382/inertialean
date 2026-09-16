#!/bin/bash

# Setup Daily Reports Cron Jobs
# This script sets up cron jobs to run daily reports at 6:00 AM India time

echo "Setting up daily reports cron jobs..."

# Get the current directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Create log directory if it doesn't exist
mkdir -p "$SCRIPT_DIR/logs"

# Remove existing cron jobs for these reports
(crontab -l 2>/dev/null | grep -v "daily_leads_report.py" | grep -v "daily_monthly_investments_report.py") | crontab -

# Add new cron jobs
# 6:00 AM India time = 0:30 UTC (IST is UTC+5:30)
(crontab -l 2>/dev/null; echo "30 0 * * * cd $SCRIPT_DIR && /usr/bin/python3 $SCRIPT_DIR/daily_leads_report.py >> $SCRIPT_DIR/logs/daily_leads_report.log 2>&1") | crontab -
(crontab -l 2>/dev/null; echo "35 0 * * * cd $SCRIPT_DIR && /usr/bin/python3 $SCRIPT_DIR/daily_monthly_investments_report.py >> $SCRIPT_DIR/logs/daily_monthly_investments_report.log 2>&1") | crontab -

echo "✅ Daily reports cron jobs have been set up successfully!"
echo ""
echo "📅 Schedule:"
echo "   - Daily Leads Report: 6:00 AM IST (0:30 UTC)"
echo "   - Daily Monthly Investments Report: 6:05 AM IST (0:35 UTC)"
echo ""
echo "📧 Recipients:"
echo "   - onboarding@equities4wealth.com"
echo "   - anshul@equities4wealth.com"
echo ""
echo "📁 Log files:"
echo "   - $SCRIPT_DIR/logs/daily_leads_report.log"
echo "   - $SCRIPT_DIR/logs/daily_monthly_investments_report.log"
echo ""
echo "To view current cron jobs: crontab -l"
echo "To remove cron jobs: crontab -r"
