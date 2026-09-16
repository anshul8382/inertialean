# Daily Reports System

This document describes the automated daily email reports system for the Inertia Investment Management System.

## Overview

The system generates and sends two daily email reports at 6:00 AM India time:

1. **Daily Leads Report** - Shows all leads bucketed by status (active and inactive)
2. **Daily Monthly Investments Report** - Shows pending investments bucketed by workflow status

## Reports

### 1. Daily Leads Report

**File**: `daily_leads_report.py`  
**Schedule**: 6:00 AM IST (0:30 UTC)  
**Recipients**: onboarding@equities4wealth.com, anshul@equities4wealth.com

**Content**:
- Summary statistics (total active/inactive leads, unique statuses)
- Alert leads with SLA violations requiring immediate attention
- Active leads bucketed by status with detailed information
- Inactive leads bucketed by status with detailed information
- Each lead shows: Name, Email, Phone, Source, Next Step, Notes, SLA Missed (for alerts), Created Date, Last Updated

**Lead Information Displayed**:
- Name
- Email
- Phone
- Source
- Next Step (workflow stage or status-based next action)
- Notes (from lead notes or latest call log)
- SLA Missed (specific SLA violations for alert leads)
- Created Date
- Last Updated Date

### 2. Daily Monthly Investments Report

**File**: `daily_monthly_investments_report.py`  
**Schedule**: 6:05 AM IST (0:35 UTC)  
**Recipients**: onboarding@equities4wealth.com, anshul@equities4wealth.com

**Content**:
- Summary statistics (total pending investments, total amount, workflow stages)
- Pending investments bucketed by workflow status
- Only shows investments that require attention (not completed)
- Each investment shows: Client, Amount, Investment Date, Created Date, Status

**Workflow Stages Covered**:
- **FUNDS** - Funds Received (Yellow)
- **RECOS** - Recommendations Generated (Blue)
- **NOTIFY** - Client Notified (Purple)
- **EXEC** - Trades Executed (Orange)
- **UPDATE** - Portfolio Updated (Teal)
- **NO_WORKFLOW** - No Workflow Created (Gray)

## Email Configuration

**SMTP Server**: 65-254-81-55.cprapid.com:587  
**Sender**: anshul@inertiainvest.in  
**Authentication**: TLS enabled  
**Format**: HTML email with embedded CSS styling

## Scheduling

The reports are scheduled using cron jobs:

```bash
# Daily Leads Report - 6:00 AM IST
30 0 * * * cd /home/inertia/app && /usr/bin/python3 /home/inertia/app/daily_leads_report.py >> /home/inertia/app/logs/daily_leads_report.log 2>&1

# Daily Monthly Investments Report - 6:05 AM IST
35 0 * * * cd /home/inertia/app && /usr/bin/python3 /home/inertia/app/daily_monthly_investments_report.py >> /home/inertia/app/logs/daily_monthly_investments_report.log 2>&1
```

## Setup

### Initial Setup

1. **Make scripts executable**:
   ```bash
   chmod +x daily_leads_report.py daily_monthly_investments_report.py setup_daily_reports_cron.sh
   ```

2. **Set up cron jobs**:
   ```bash
   ./setup_daily_reports_cron.sh
   ```

### Manual Testing

**Test Leads Report**:
```bash
python3 daily_leads_report.py
```

**Test Monthly Investments Report**:
```bash
python3 daily_monthly_investments_report.py
```

## Log Files

- **Leads Report Log**: `/home/inertia/app/logs/daily_leads_report.log`
- **Monthly Investments Report Log**: `/home/inertia/app/logs/daily_monthly_investments_report.log`

## Monitoring

### Check Cron Jobs
```bash
crontab -l
```

### View Recent Logs
```bash
# View leads report logs
tail -f /home/inertia/app/logs/daily_leads_report.log

# View monthly investments report logs
tail -f /home/inertia/app/logs/daily_monthly_investments_report.log
```

### Check for Errors
```bash
# Check for errors in leads report
grep -i error /home/inertia/app/logs/daily_leads_report.log

# Check for errors in monthly investments report
grep -i error /home/inertia/app/logs/daily_monthly_investments_report.log
```

## Troubleshooting

### Common Issues

1. **Email not sending**: Check SMTP credentials and server connectivity
2. **No data in report**: Verify there are leads/investments in the database
3. **Cron job not running**: Check cron service and file permissions
4. **Encoding errors**: Reports use "Rs." instead of "₹" to avoid encoding issues

### Manual Execution

If cron jobs fail, you can manually run the reports:

```bash
cd /home/inertia/app
python3 daily_leads_report.py
python3 daily_monthly_investments_report.py
```

### Database Connectivity

The reports require database access. Ensure:
- MySQL service is running
- Database credentials are correct
- Application can connect to the database

## Customization

### Modify Report Content

Edit the respective Python files to:
- Add/remove columns in the reports
- Change email styling
- Modify status descriptions
- Add additional data sources

### Change Schedule

To change the report timing:
1. Remove existing cron jobs: `crontab -r`
2. Edit `setup_daily_reports_cron.sh` and change the cron expressions
3. Re-run setup: `./setup_daily_reports_cron.sh`

### Add Recipients

To add more email recipients, modify the `recipient_emails` list in both Python files.

## Files

- `daily_leads_report.py` - Leads report script
- `daily_monthly_investments_report.py` - Monthly investments report script
- `setup_daily_reports_cron.sh` - Cron job setup script
- `DAILY_REPORTS_README.md` - This documentation file

## Support

For issues or questions about the daily reports system, check the log files first and then contact the development team.
