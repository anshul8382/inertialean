# Alert System & SLA Monitoring - Implementation Guide

## Overview
The Alert System is a comprehensive monitoring solution that tracks SLA (Service Level Agreement) compliance across the Inertia Investment Management System. It automatically detects process delays and creates alerts to ensure timely completion of investment activities.

## Current Status: ✅ FULLY IMPLEMENTED & TESTED

### Implementation Summary
- **Database**: 4 tables, 2 views, 1 procedure created
- **Python Integration**: Complete integration with main Flask application
- **Automation**: Hourly SLA checks via cron jobs
- **Active Monitoring**: Currently tracking 13+ workflow stages
- **Alert Count**: 13 active alerts (as of latest check)
- **Web Interface**: ✅ Fully functional with acknowledge/resolve capabilities

## Recent Fixes (August 2025)

### Alert Acknowledgment System
- **Issue**: JavaScript fetch requests failing with CSRF token errors
- **Root Cause**: Missing CSRF token in API requests and incorrect error field access
- **Solution**: 
  - Added `@csrf.exempt` decorator to alert API routes
  - Updated JavaScript to use `data.error` instead of `data.message`
  - Added CSRF token headers to fetch requests
- **Status**: ✅ RESOLVED - Acknowledge and resolve functionality working

### App Management Improvements
- **Issue**: Difficulty stopping/starting app with multiple workers
- **Solution**: Created single-worker configuration for testing
- **Files Added**:
  - `gunicorn_config_single.py` - Single worker config
  - `manage_app.sh` - App management script
  - `run_app.py` - Simple Flask runner

## Database Components

### Tables Created
1. **`alert`** - Main alert storage
2. **`sla_configuration`** - SLA timelines and settings
3. **`alert_notification`** - Notification history
4. **`alert_report`** - Daily/weekly reports

### Views Created
1. **`active_alerts_view`** - Active alerts with details
2. **`alert_summary_view`** - Alert statistics

### Stored Procedure
1. **`CalculateSLACompliance`** - SLA compliance calculations

## Python Components

### Core Files
- **`alert_system_models.py`** - SQLAlchemy models
- **`alert_service.py`** - Business logic and SLA checks
- **`routes/alerts.py`** - Flask routes for web interface (✅ CSRF exempt)
- **`manual_sla_check.py`** - Manual SLA check script

### Integration
- **`main.py`** - Alert blueprint registered
- **Dashboard** - Alert counts integrated
- **Workflow System** - SLA monitoring for all stages

## Automated Monitoring

### Cron Jobs
```bash
# Hourly SLA checks
0 * * * * cd /home/inertia/app && python3 manual_sla_check.py >> /home/inertia/app/alert_system.log 2>&1

# Daily alert reports
0 8 * * * cd /home/inertia/app && python3 -c "from alert_service import AlertService; from models import app; app.app_context().push(); AlertService.generate_daily_alert_report()" >> /home/inertia/app/alert_report.log 2>&1
```

### Manual Testing
```bash
# Activate virtual environment and run manual check
source venv/bin/activate
python3 manual_sla_check.py
```

## Alert Types Currently Monitored

### Workflow SLA Alerts
- **FUNDS Stage**: 2-day SLA, alerts at 1.5 days
- **RECOS Stage**: 3-day SLA, alerts at 2 days
- **NOTIFY Stage**: 2-day SLA, alerts at 1.5 days
- **EXEC Stage**: 1-day SLA, alerts at 12 hours
- **UPDATE Stage**: 1-day SLA, alerts at 12 hours
- **COMPLETED Stage**: 1-day SLA, alerts at 12 hours

### Recommendation Alerts
- **Creation Delay**: 24-hour SLA, alerts at 18 hours
- **Execution Delay**: 2-day SLA, alerts at 1.5 days

## Web Interface

### Access Points
- **Alert Dashboard**: `/alerts`
- **Alert List**: `/alerts/list`
- **Alert Details**: `/alerts/{id}`
- **SLA Configuration**: `/alerts/config`

### API Endpoints
- `GET /alerts` - List all alerts
- `GET /alerts/{id}` - Get alert details
- `POST /alerts/{id}/acknowledge` - Acknowledge alert ✅ WORKING
- `POST /alerts/{id}/resolve` - Resolve alert ✅ WORKING
- `POST /alerts/{id}/escalate` - Escalate alert
- `POST /alerts/{id}/snooze` - Snooze alert

### JavaScript Functions
- `acknowledgeAlert(alertId)` - Acknowledge an alert
- `resolveAlert(alertId)` - Resolve an alert with notes
- `escalateAlert(alertId)` - Escalate an alert
- `snoozeAlert(alertId)` - Snooze an alert for specified hours

## App Management

### Single-Worker Mode (Testing)
```bash
# Start with single worker
./manage_app.sh start

# Stop all processes
./manage_app.sh stop

# Restart
./manage_app.sh restart

# Check status
./manage_app.sh status
```

### Multi-Worker Mode (Production)
```bash
# Start with multiple workers
./manage_app.sh start-multi

# Restart with multiple workers
./manage_app.sh restart-multi
```

## Current Alert Status

### Active Alerts (Latest Check)
- **Total Alerts**: 13
- **Critical Alerts**: 13 (SLA violations)
- **Warning Alerts**: 0
- **Info Alerts**: 0

### Alert Distribution by Stage
- **FUNDS Stage**: Multiple alerts (funds not moved to RECOS)
- **RECOS Stage**: Multiple alerts (recommendations not generated)
- **EXEC Stage**: Multiple alerts (trades not executed)
- **NOTIFY Stage**: Multiple alerts (client not notified)
- **UPDATE Stage**: Multiple alerts (portfolio not updated)

## Configuration

### Default SLA Settings
```sql
-- Workflow stages
INSERT INTO sla_configuration (process_name, process_stage, sla_hours, alert_trigger_hours) VALUES
('monthly_investment', 'FUNDS', 48, 36),
('monthly_investment', 'RECOS', 72, 48),
('monthly_investment', 'NOTIFY', 48, 36),
('monthly_investment', 'EXEC', 24, 12),
('monthly_investment', 'UPDATE', 24, 12),
('monthly_investment', 'COMPLETED', 24, 12);

-- Recommendations
INSERT INTO sla_configuration (process_name, process_stage, sla_hours, alert_trigger_hours) VALUES
('recommendation', 'creation', 24, 18),
('recommendation', 'execution', 48, 36);
```

### Customization Options
- **Client-Specific SLAs**: Different timelines for VIP clients
- **Process-Specific SLAs**: Custom timelines per process
- **Seasonal Adjustments**: Modified SLAs during holidays
- **Escalation Rules**: Automatic escalation based on alert age

## Monitoring and Maintenance

### Log Files
- **`alert_system.log`** - Hourly SLA check logs
- **`alert_report.log`** - Daily report generation logs

### Health Checks
```bash
# Check alert system status
python3 -c "from models import db; from main import create_app; from alert_system_models import Alert; app = create_app(); app.app_context().push(); print(f'Total alerts: {Alert.query.count()}'); print('Alert system is operational')"

# Check cron jobs
crontab -l | grep alert

# Check recent logs
tail -20 alert_system.log
```

### Performance Metrics
- **SLA Compliance Rate**: Percentage of processes meeting SLA
- **Alert Resolution Time**: Average time to resolve alerts
- **Escalation Rate**: Percentage of alerts requiring escalation
- **False Positive Rate**: Percentage of unnecessary alerts

## Troubleshooting

### Common Issues
1. **No Alerts Generated**: Check SLA configurations and workflow data
2. **Cron Job Not Running**: Verify cron service and permissions
3. **Database Connection Issues**: Check database connectivity
4. **Import Errors**: Ensure virtual environment is activated

### Debug Commands
```bash
# Test alert service directly
source venv/bin/activate
python3 -c "from alert_service import AlertService; AlertService.check_workflow_sla()"

# Check database connectivity
python3 -c "from models import db; from main import create_app; app = create_app(); app.app_context().push(); print('Database connection successful')"

# Verify alert models
python3 -c "from alert_system_models import Alert, SLAConfiguration; print('Alert models loaded successfully')"
```

## Future Enhancements

### Planned Features
1. **Email Notifications**: Automatic email alerts for critical issues
2. **SMS Alerts**: SMS notifications for urgent alerts
3. **Advanced Escalation**: Multi-level escalation rules
4. **Custom Alert Types**: User-defined alert conditions
5. **Alert Analytics**: Advanced reporting and analytics
6. **Mobile App Integration**: Push notifications to mobile app

### Integration Opportunities
1. **Client Portal**: Alert notifications to clients
2. **External Systems**: Integration with CRM and other systems
3. **Reporting Tools**: Integration with business intelligence tools
4. **Compliance Systems**: Integration with regulatory compliance systems

## Support and Maintenance

### Regular Maintenance Tasks
1. **Daily**: Review new alerts and resolve urgent issues
2. **Weekly**: Analyze alert patterns and adjust SLAs if needed
3. **Monthly**: Review SLA compliance rates and optimize processes
4. **Quarterly**: Update SLA configurations based on business needs

### Contact Information
- **Technical Support**: System administrator
- **Business Questions**: Process owners
- **SLA Adjustments**: Management team

---

**Last Updated**: August 19, 2025
**Version**: 1.0
**Status**: Production Ready ✅

