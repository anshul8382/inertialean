# Alert System Troubleshooting Guide

## Overview
This document provides troubleshooting information for the Alert System, including recent fixes and common issues.

## Recent Fixes (August 2025)

### Issue: Alert Acknowledgment Not Working
**Problem**: Users were unable to acknowledge or resolve alerts due to JavaScript errors.

**Symptoms**:
- Clicking "Acknowledge Alert" button showed "Error acknowledging alert: undefined"
- Clicking "Resolve Alert" button showed "Error resolving alert: undefined"
- Browser console showed CSRF token errors

**Root Causes**:
1. **CSRF Token Missing**: JavaScript fetch requests weren't including CSRF tokens
2. **Incorrect Error Field**: JavaScript was looking for `data.message` but backend returned `data.error`

**Solution Applied**:
1. **CSRF Exemption**: Added `@csrf.exempt` decorator to alert API routes in `routes/alerts.py`
2. **Error Field Fix**: Updated JavaScript to use `data.error` instead of `data.message`
3. **CSRF Headers**: Added CSRF token headers to fetch requests (as backup)

**Files Modified**:
- `routes/alerts.py` - Added CSRF exemption decorators
- `templates/alerts/view_alert.html` - Fixed JavaScript error handling
- `templates/alerts/list_alerts.html` - Fixed JavaScript error handling

**Status**: ✅ RESOLVED

### Issue: App Management Difficulties
**Problem**: Difficulty stopping and starting the Flask app with multiple workers.

**Solution Applied**:
1. **Single-Worker Config**: Created `gunicorn_config_single.py` for testing
2. **Management Script**: Created `manage_app.sh` for easy app management
3. **Simple Runner**: Created `run_app.py` for development mode

**Commands**:
```bash
# Single worker (testing)
./manage_app.sh start
./manage_app.sh stop
./manage_app.sh restart

# Multi worker (production)
./manage_app.sh start-multi
./manage_app.sh restart-multi
```

**Status**: ✅ RESOLVED

## Common Issues and Solutions

### 1. CSRF Token Errors
**Error**: "The CSRF token is missing" or "The CSRF session token is missing"

**Solutions**:
- Clear browser cache and cookies
- Log out and log back in
- Refresh the page before attempting action
- Check if you're logged in properly

### 2. Permission Denied Errors
**Error**: "Access denied" when trying to acknowledge/resolve alerts

**Solutions**:
- Ensure you have proper permissions for the alert
- Check if the alert is assigned to you (if not a manager)
- Contact administrator for permission issues

### 3. Alert Not Found
**Error**: "Alert not found" or 404 errors

**Solutions**:
- Refresh the alerts page
- Check if the alert was already resolved by someone else
- Verify the alert ID in the URL

### 4. JavaScript Errors
**Error**: Console shows JavaScript errors

**Solutions**:
- Check browser console for specific error messages
- Ensure JavaScript is enabled
- Try a different browser
- Clear browser cache

## Testing the Alert System

### Manual Testing
```bash
# Test alert acknowledgment
python3 test_alert_acknowledge.py

# Check active alerts
python3 -c "from main import app; from alert_system_models import Alert; app.app_context().push(); alerts = Alert.query.filter_by(status='active').all(); print(f'Active alerts: {len(alerts)}')"
```

### Maintenance Scripts
- `scripts/cleanup_workflow_alerts.py`: Keeps only the latest workflow stage alert per workflow and resolves older ones.
- `scripts/close_duplicate_alerts.py`: Resolves duplicate alerts (same `client_id` + `alert_type` + `alert_subtype`) by marking extras as **resolved** with a "duplicate" resolution note.
  - Dry-run (no writes):
    - `python scripts/close_duplicate_alerts.py --dry-run`
  - Apply changes:
    - `python scripts/close_duplicate_alerts.py --apply --user-id 1`

### Web Interface Testing
1. Navigate to `/alerts`
2. Click on an active alert
3. Try acknowledging the alert
4. Try resolving the alert with notes
5. Verify the alert status changes

## Debugging Steps

### 1. Check App Status
```bash
./manage_app.sh status
```

### 2. Check Logs
```bash
# Gunicorn logs
tail -f gunicorn.log

# Alert system logs
tail -f alert_system.log
```

### 3. Check Database
```bash
# Connect to database and check alerts
mysql -u inertia_admin -p inertia_app2025
SELECT * FROM alert WHERE status = 'active' LIMIT 5;
```

### 4. Test API Endpoints
```bash
# Test acknowledge endpoint
curl -X POST http://localhost:5000/alerts/1/acknowledge \
  -H "Content-Type: application/json" \
  -H "X-CSRFToken: your-token-here"
```

## Prevention

### Best Practices
1. **Regular Testing**: Test alert functionality after deployments
2. **Log Monitoring**: Monitor logs for errors
3. **User Training**: Train users on proper alert management
4. **Backup Configurations**: Keep backup configurations for different environments

### Monitoring
- Check alert system logs daily
- Monitor SLA compliance rates
- Review alert resolution times
- Track user adoption of alert management

## Support

### Contact Information
- **System Administrator**: For permission and configuration issues
- **Technical Support**: For technical problems
- **Development Team**: For bug reports and feature requests

### Escalation Process
1. Check this troubleshooting guide
2. Try the solutions listed above
3. Check system logs for errors
4. Contact technical support with specific error messages
5. Escalate to development team if issue persists

---

*Last Updated: August 2025*
*Version: 1.0*


