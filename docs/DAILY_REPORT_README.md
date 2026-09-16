# Daily Workflow Report System

This system automatically generates and sends daily reports of all active workflows to `anshul@equities4wealth.com`.

## Features

- **Stage-wise Organization**: Groups clients by workflow stage (FUNDS, RECOS, NOTIFY, EXEC, UPDATE)
- **Comprehensive Data**: Shows client names, amounts, investment dates, action counts, and latest notes
- **Professional HTML Format**: Beautiful, responsive email with color-coded stages
- **Automated Delivery**: Runs daily at 9:00 AM via cron job
- **Easy Management**: Simple commands to test, view logs, and manage the system

## Report Content

Each daily report includes:

### Summary Section
- Total active workflows
- Total amount involved across all stages
- Number of stages with active workflows

### Stage-wise Details
For each workflow stage:
- **Client Information**: Name, email, planned amount, actual amount
- **Investment Details**: Investment date, number of actions taken
- **Latest Notes**: Most recent workflow action notes
- **Stage Subtotal**: Total amount for that stage

### Grand Total
- Overall total amount across all active workflows

## Files

- `daily_workflow_report.py` - Main report generation script
- `setup_daily_report_cron.sh` - Cron job setup script
- `manage_daily_report.py` - Management and testing script
- `daily_report.log` - Log file for cron job execution

## Usage

### Quick Commands

```bash
# Test the report (send immediately)
python3 manage_daily_report.py test

# View latest logs
python3 manage_daily_report.py logs

# Check cron job status
python3 manage_daily_report.py status

# Setup cron job
python3 manage_daily_report.py setup

# Remove cron job
python3 manage_daily_report.py remove

# Show help
python3 manage_daily_report.py help
```

### Manual Testing

```bash
# Activate virtual environment and run
source venv/bin/activate
python3 daily_workflow_report.py
```

### Cron Job Details

- **Schedule**: Daily at 9:00 AM
- **Command**: `0 9 * * * cd /home/inertia/app && /usr/bin/python3 /home/inertia/app/daily_workflow_report.py >> /home/inertia/app/daily_report.log 2>&1`
- **Log File**: `/home/inertia/app/daily_report.log`

## Email Configuration

- **SMTP Server**: 65-254-81-55.cprapid.com:587
- **Sender**: anshul@inertiainvest.in
- **Recipient**: anshul@equities4wealth.com
- **Format**: HTML email with embedded CSS styling

## Workflow Stages

The report covers these workflow stages:

1. **FUNDS** - Awaiting Funds (Yellow)
2. **RECOS** - Generating Recommendations (Cyan)
3. **NOTIFY** - Client Notification (Purple)
4. **EXEC** - Trade Execution (Orange)
5. **UPDATE** - Portfolio Update (Teal)
6. **COMPLETED** - Completed (Green) - Not included in active reports

## Troubleshooting

### Common Issues

1. **Email not sending**: Check SMTP credentials and server connectivity
2. **No data in report**: Verify there are active workflows in the database
3. **Cron job not running**: Check cron service and file permissions
4. **Encoding errors**: Report uses "Rs." instead of "₹" to avoid encoding issues

### Log Analysis

```bash
# View recent logs
tail -f daily_report.log

# Check for errors
grep -i error daily_report.log

# View cron job logs
grep CRON /var/log/syslog
```

### Database Queries

The report queries these tables:
- `workflow` - Main workflow information
- `workflow_action` - Workflow actions and notes
- `monthly_investment` - Investment details
- `client` - Client information

## Customization

### Modify Report Content

Edit `daily_workflow_report.py` to:
- Add/remove columns in the report
- Change email styling
- Modify stage descriptions
- Add additional data sources

### Change Schedule

To change the report timing:
1. Remove existing cron job: `python3 manage_daily_report.py remove`
2. Edit `setup_daily_report_cron.sh` and change the cron expression
3. Re-run setup: `python3 manage_daily_report.py setup`

### Add Recipients

To add more email recipients, modify the `send_email_report()` function in `daily_workflow_report.py`.

## Security Notes

- Email credentials are hardcoded in the script
- Consider using environment variables for production
- Log files may contain sensitive information
- Ensure proper file permissions on scripts

## Support

For issues or modifications:
1. Check the logs first: `python3 manage_daily_report.py logs`
2. Test manually: `python3 manage_daily_report.py test`
3. Verify cron job: `python3 manage_daily_report.py status`

