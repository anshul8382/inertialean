# Monthly Holdings Cycle System

## Overview
Automated system for processing client holdings on a monthly cycle, detecting mismatches between transactions and holdings.

## How It Works

### Cycle Schedule
- **Frequency**: Monthly cycle, weekly processing
- **Processing**: All clients every Sunday at 2 AM IST
- **Report email**: Monday at 2:30 AM IST (Sunday run results)
- **Cycle Start**: 1st of every month at 1 AM IST (entries scheduled for first Sunday)

## Scripts

### 1. `start_monthly_cycle.py`
**Purpose**: Start a new monthly cycle for all clients  
**Schedule**: 1st of every month at 1 AM IST (7:30 PM EDT previous day)  
**Actions**:
- Creates cycle entries for all active clients
- Schedules all clients for the first Sunday of the month
- Marks all entries as 'pending'

### 2. `daily_holdings_processor.py`
**Purpose**: Process all clients in the current monthly cycle  
**Schedule**: Sunday at 2 AM IST  
**Actions**:
- Processes every client in the current month's cycle
- Updates holdings from transactions
- Detects mismatches (quantity, price, missing holdings)
- Updates cycle status

### 3. `cycle_status_monitor.py`
**Purpose**: Generate cycle status reports  
**Schedule**: Weekly on Sundays at 3 AM IST (5:30 PM EDT Saturday)  
**Actions**:
- Shows cycle progress
- Lists clients with mismatches
- Indicates cycle completion status

## Manual Execution

### Start a new cycle manually:
```bash
cd /home/inertia/app
python scripts/start_monthly_cycle.py
```

### Process all clients manually (weekly run):
```bash
cd /home/inertia/app
python scripts/daily_holdings_processor.py
```

### Check cycle status:
```bash
cd /home/inertia/app
python scripts/cycle_status_monitor.py
```

## Mismatch Detection

The system detects:
1. **Quantity Mismatches**: Holdings quantity doesn't match transactions
2. **Price Mismatches**: Average price calculation errors
3. **Missing Holdings**: Transactions exist but no holding record
4. **Extra Holdings**: Holdings without corresponding transactions

## Database Table

### `monthly_holdings_cycle`
- `id`: Primary key
- `cycle_id`: Cycle identifier (YYYY-MM format)
- `client_id`: Client ID
- `processing_date`: Scheduled processing date
- `status`: pending, processing, completed, failed
- `processed_at`: Actual processing timestamp
- `error_message`: Error details if failed
- `mismatches_found`: Number of mismatches detected
- `created_at`: Record creation time
- `updated_at`: Last update time

## Logs

All scripts log to:
- `/home/inertia/app/logs/monthly_cycle.log` - Cycle creation
- `/home/inertia/app/logs/daily_processor.log` - Daily processing
- `/home/inertia/app/logs/cycle_status.log` - Status reports
- `/home/inertia/app/logs/holdings_processor.log` - Detailed processing logs

## Monitoring

### Check recent logs:
```bash
tail -f /home/inertia/app/logs/holdings_processor.log
```

### View cycle progress:
```bash
python scripts/cycle_status_monitor.py
```

### Database query:
```sql
SELECT cycle_id, status, COUNT(*) as count 
FROM monthly_holdings_cycle 
GROUP BY cycle_id, status;
```

## Benefits

1. ✅ **Automated**: No manual intervention needed
2. ✅ **Predictable**: Always know when cycle completes
3. ✅ **Scalable**: Works for any number of clients
4. ✅ **Transparent**: Full logging and status tracking
5. ✅ **Reliable**: Failed clients can be retried
6. ✅ **Mismatch Detection**: Identify data quality issues proactively

## Troubleshooting

### Cycle not starting:
- Check cron job is configured: `crontab -l`
- Check logs: `tail /home/inertia/app/logs/monthly_cycle.log`

### Clients not processing:
- Check daily logs: `tail /home/inertia/app/logs/daily_processor.log`
- Verify processing date: Check database
- Manually run processor: `python scripts/daily_holdings_processor.py`

### High mismatch count:
- Review detailed logs: `tail /home/inertia/app/logs/holdings_processor.log`
- Check transaction data quality
- Verify holdings calculations

## Support

For issues or questions, check:
1. Log files in `/home/inertia/app/logs/`
2. Database status in `monthly_holdings_cycle` table
3. Cycle status using `cycle_status_monitor.py`

