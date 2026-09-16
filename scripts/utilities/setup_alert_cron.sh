#!/bin/bash

# Setup Alert System Cron Jobs
# This script sets up automated SLA checks and alert reports

echo "Setting up Alert System Cron Jobs..."

# Get app root (repo root)
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Add hourly SLA check to crontab (every hour at minute 0)
(crontab -l 2>/dev/null; echo "0 * * * * cd $APP_DIR && python3 manual_sla_check.py >> $APP_DIR/logs/alert_system.log 2>&1") | crontab -

echo "✅ Alert system cron jobs have been set up!"
echo "📅 Hourly SLA checks will run every hour"
echo "📝 Logs will be saved to:"
echo "   - $APP_DIR/logs/alert_system.log (SLA checks)"
echo ""
echo "To view current cron jobs: crontab -l"
echo "To test manually: cd $APP_DIR && python3 manual_sla_check.py"



