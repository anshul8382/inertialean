# Cron Job Management System

This document describes the comprehensive cron job management system for the Inertia Investment Management System.

## Overview

The cron management system provides a web-based interface to manage all automated tasks and scheduled reports. It allows administrators to:

- View all cron jobs and their current status
- Enable/disable cron jobs
- Update job schedules
- Test jobs manually
- View job logs
- Monitor system information

## Access

Navigate to **Settings → System Settings** in the main navigation menu, or directly access:
- Main Settings: `/settings/`
- Cron Jobs Management: `/settings/cron-jobs`
- System Information: `/settings/system-info`

## Managed Cron Jobs

### Reports Category

#### 1. Daily Workflow Report
- **Script**: `daily_workflow_report.py`
- **Schedule**: `0 9 * * *` (9:00 AM IST)
- **Description**: Generates and sends daily workflow status report
- **Log File**: `daily_report.log`
- **Status**: Active

#### 2. Daily Leads Report
- **Script**: `daily_leads_report.py`
- **Schedule**: `30 0 * * *` (6:00 AM IST)
- **Description**: Generates and sends daily leads report bucketed by status
- **Log File**: `logs/daily_leads_report.log`
- **Recipients**: onboarding@equities4wealth.com, anshul@equities4wealth.com
- **Status**: Active

#### 3. Daily Monthly Investments Report
- **Script**: `daily_monthly_investments_report.py`
- **Schedule**: `35 0 * * *` (6:05 AM IST)
- **Description**: Generates and sends daily pending investments report
- **Log File**: `logs/daily_monthly_investments_report.log`
- **Recipients**: onboarding@equities4wealth.com, anshul@equities4wealth.com
- **Status**: Active

### Monitoring Category

#### 4. Hourly SLA Check
- **Script**: `manual_sla_check.py`
- **Schedule**: `0 * * * *` (Every hour)
- **Description**: Checks for SLA violations and generates alerts
- **Log File**: `alert_system.log`
- **Status**: Active

#### 5. Daily Alert Report
- **Script**: `alert_service.py`
- **Schedule**: `0 8 * * *` (8:00 AM IST)
- **Description**: Generates daily alert summary report
- **Log File**: `alert_report.log`
- **Status**: Active

## Features

### 1. Job Status Management
- **Enable/Disable**: Toggle jobs on/off with a single click
- **Real-time Status**: See which jobs are currently active
- **Visual Indicators**: Color-coded badges show job status

### 2. Schedule Management
- **Update Schedules**: Modify cron schedules through web interface
- **Human-readable Descriptions**: See what each schedule means
- **Validation**: Basic cron format validation

### 3. Testing
- **Manual Test Runs**: Test any job immediately
- **Real-time Feedback**: See test results instantly
- **Error Handling**: Clear error messages for failed tests

### 4. Log Management
- **Log Viewer**: View last 100 lines of any job's log
- **Auto-refresh**: Logs refresh automatically every 30 seconds
- **Search**: Easy navigation through log files

### 5. System Monitoring
- **System Information**: CPU, memory, disk usage
- **Application Stats**: Python version, platform info
- **Quick Actions**: Test email, refresh info, view logs

## Usage

### Viewing Cron Jobs
1. Navigate to **Settings → System Settings**
2. Click **"Manage Cron Jobs"**
3. View jobs organized by category (Reports, Monitoring)

### Enabling/Disabling Jobs
1. In the cron jobs table, click the **Play/Pause** button
2. The job status will update immediately
3. Page will refresh to show new status

### Updating Schedules
1. Click the **Clock** icon next to any job
2. Enter new cron schedule in format: `minute hour day month weekday`
3. Click **"Update Schedule"**
4. Job will be updated and page will refresh

### Testing Jobs
1. Click the **Play** button (test icon) next to any job
2. Wait for test to complete
3. View results in popup message

### Viewing Logs
1. Click the **File** icon next to any job
2. View last 100 lines of the job's log file
3. Logs auto-refresh every 30 seconds

## Cron Schedule Format

The system uses standard cron format: `minute hour day month weekday`

### Common Examples
- `0 9 * * *` - Daily at 9:00 AM
- `0 * * * *` - Every hour
- `0 0 * * *` - Daily at midnight
- `0 6 * * 1` - Every Monday at 6:00 AM
- `*/15 * * * *` - Every 15 minutes

### Time Zones
- All schedules are in **India Standard Time (IST)**
- Server timezone is automatically detected and displayed

## Troubleshooting

### Common Issues

#### 1. Job Not Running
- Check if job is enabled (green badge)
- Verify cron service is running: `systemctl status crond`
- Check job logs for errors
- Test job manually to identify issues

#### 2. Permission Errors
- Ensure scripts are executable: `chmod +x script.py`
- Check file ownership and permissions
- Verify log directories exist and are writable

#### 3. Import Errors
- Check Python path and dependencies
- Verify all required modules are installed
- Test imports manually in Python shell

#### 4. Email Not Sending
- Verify SMTP settings in configuration
- Check network connectivity
- Test email functionality manually

### Debugging Steps

1. **Check Job Status**: Use the web interface to see if job is enabled
2. **View Logs**: Check the job's log file for error messages
3. **Test Manually**: Use the test button to run job immediately
4. **Check System**: Verify system resources and services
5. **Review Schedule**: Ensure cron schedule is correct

### Manual Commands

```bash
# View all cron jobs
crontab -l

# Edit cron jobs manually
crontab -e

# Check cron service status
systemctl status crond

# View specific log file
tail -f /home/inertia/app/logs/daily_leads_report.log

# Test a script manually
cd /home/inertia/app && python3 daily_leads_report.py
```

## Security

### Access Control
- Only authenticated users can access settings
- Admin privileges may be required for certain operations
- All actions are logged for audit purposes

### File Permissions
- Scripts should be executable by the web server user
- Log files should be writable by the cron user
- Sensitive configuration is protected

## Monitoring

### System Health
- Monitor disk usage to prevent log file issues
- Check memory usage for long-running jobs
- Verify cron service is running

### Job Performance
- Review log files regularly for errors
- Monitor job execution times
- Check for failed email deliveries

### Alerts
- Set up monitoring for critical jobs
- Configure alerts for system issues
- Monitor log file sizes

## Best Practices

### Job Design
- Keep jobs focused and single-purpose
- Include proper error handling
- Use appropriate logging levels
- Set reasonable timeouts

### Scheduling
- Avoid overlapping resource-intensive jobs
- Consider system load when scheduling
- Use descriptive job names and descriptions
- Document schedule changes

### Maintenance
- Regularly review and clean up old logs
- Monitor job performance and adjust schedules
- Keep scripts and dependencies updated
- Test changes in development environment first

## Support

For issues with the cron management system:

1. Check the troubleshooting section above
2. Review job logs for specific error messages
3. Test jobs manually to isolate issues
4. Contact the development team with detailed error information

## Files

- `cron_manager.py` - Core cron management functionality
- `routes/settings.py` - Web interface routes
- `templates/settings/` - Web interface templates
- `daily_leads_report.py` - Leads report script
- `daily_monthly_investments_report.py` - Investments report script
- `setup_daily_reports_cron.sh` - Initial setup script
