# Alert System Changelog

## Version 1.1 - August 2025

### 🐛 Bug Fixes
- **Fixed Alert Acknowledgment**: Resolved CSRF token issues preventing alert acknowledgment and resolution
- **Fixed Error Messages**: Corrected JavaScript to display proper error messages from backend
- **Improved App Management**: Added single-worker configuration for easier testing and debugging

### ✨ New Features
- **App Management Script**: Created `manage_app.sh` for easy app start/stop/restart
- **Single-Worker Testing**: Added `gunicorn_config_single.py` for development testing
- **Troubleshooting Guide**: Created comprehensive troubleshooting documentation

### 🔧 Technical Changes
- **CSRF Exemption**: Added `@csrf.exempt` decorator to alert API routes
- **JavaScript Updates**: Updated fetch requests to include CSRF tokens and proper error handling
- **Template Improvements**: Enhanced error message display in alert templates

### 📚 Documentation
- **Updated ALERT_SYSTEM_README.md**: Added recent fixes and app management sections
- **Updated USER_MANUAL.md**: Added alert management and troubleshooting sections
- **Created ALERT_SYSTEM_TROUBLESHOOTING.md**: Comprehensive troubleshooting guide
- **Created ALERT_SYSTEM_CHANGELOG.md**: This changelog file

### 📁 Files Added
- `gunicorn_config_single.py` - Single worker gunicorn configuration
- `manage_app.sh` - App management script
- `run_app.py` - Simple Flask runner for development
- `test_alert_acknowledge.py` - Test script for alert functionality
- `ALERT_SYSTEM_TROUBLESHOOTING.md` - Troubleshooting guide
- `ALERT_SYSTEM_CHANGELOG.md` - This changelog

### 📁 Files Modified
- `routes/alerts.py` - Added CSRF exemption decorators
- `templates/alerts/view_alert.html` - Fixed JavaScript error handling and CSRF tokens
- `templates/alerts/list_alerts.html` - Fixed JavaScript error handling and CSRF tokens
- `ALERT_SYSTEM_README.md` - Added recent fixes and app management sections
- `USER_MANUAL.md` - Added alert management and troubleshooting sections

## Version 1.0 - Initial Release

### ✨ Features
- Complete alert system implementation
- SLA monitoring for workflow stages
- Automated alert generation
- Web interface for alert management
- Database integration
- Cron job automation

### 📁 Core Files
- `alert_system_models.py` - SQLAlchemy models
- `alert_service.py` - Business logic
- `routes/alerts.py` - Flask routes
- `manual_sla_check.py` - Manual testing script
- Database tables and views

---

## Testing Status

### ✅ Working Features
- Alert acknowledgment
- Alert resolution with notes
- Alert escalation
- Alert snoozing
- SLA monitoring
- Automated alert generation
- Web interface navigation
- Error message display

### 🔄 Known Limitations
- Single-user testing mode recommended
- CSRF exemption required for API endpoints
- Manual app restart needed for configuration changes

---

*For detailed information, see ALERT_SYSTEM_README.md and ALERT_SYSTEM_TROUBLESHOOTING.md*


