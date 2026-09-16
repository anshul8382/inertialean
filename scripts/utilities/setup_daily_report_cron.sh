#!/bin/bash

# Setup Daily Workflow Report Cron Job
# This script sets up a cron job to send daily reports at 9:00 AM

echo "Setting up daily workflow report cron job..."

# Get the current directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/daily_workflow_report.py"

# Make the Python script executable
chmod +x "$PYTHON_SCRIPT"

# Create a log file for the cron job
LOG_FILE="$SCRIPT_DIR/daily_report.log"

# Create the cron job entry (runs daily at 9:00 AM)
CRON_JOB="0 9 * * * cd $SCRIPT_DIR && /usr/bin/python3 $PYTHON_SCRIPT >> $LOG_FILE 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "daily_workflow_report.py"; then
    echo "Cron job already exists. Updating..."
    # Remove existing cron job
    crontab -l 2>/dev/null | grep -v "daily_workflow_report.py" | crontab -
fi

# Add the new cron job
(crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -

echo "✅ Daily workflow report cron job set up successfully!"
echo "📅 Report will be sent daily at 9:00 AM"
echo "📧 Recipient: anshul@equities4wealth.com"
echo "📝 Log file: $LOG_FILE"
echo ""
echo "To test the report manually, run:"
echo "python3 $PYTHON_SCRIPT"
echo ""
echo "To view cron jobs:"
echo "crontab -l"
echo ""
echo "To remove the cron job:"
echo "crontab -l | grep -v 'daily_workflow_report.py' | crontab -"

