# Inertia Investment Management System - Comprehensive Documentation

## Table of Contents

1. [System Overview](#system-overview)
2. [Installation & Setup](#installation--setup)
3. [User Manual](#user-manual)
4. [Cron Job Management](#cron-job-management)
5. [Daily Reports System](#daily-reports-system)
6. [Alert System](#alert-system)
7. [Workflow System](#workflow-system)
8. [Process Flows](#process-flows)
9. [Function Inventory](#function-inventory)
10. [Deployment Guide](#deployment-guide)
11. [Troubleshooting](#troubleshooting)

---

## System Overview

The Inertia Investment Management System is a comprehensive web-based platform for managing investment portfolios, clients, workflows, and automated reporting. The system provides:

- **Client Management**: Complete client lifecycle management
- **Portfolio Management**: Track investments, holdings, and performance
- **Workflow Automation**: Automated investment workflows
- **Daily Reporting**: Automated daily reports via email
- **Alert System**: Real-time alerts and notifications
- **Cron Job Management**: Automated task scheduling and execution

### Key Features

- **Multi-user Support**: Role-based access control
- **Real-time Data**: Live market data integration
- **Automated Reports**: Daily email reports
- **Workflow Management**: Investment process automation
- **Alert System**: SLA monitoring and notifications
- **Cron Job Management**: Automated task execution

### System Architecture

The system is built using:
- **Backend**: Python Flask framework
- **Database**: MySQL 8.0+
- **Frontend**: HTML, CSS, JavaScript, Bootstrap
- **Email**: Flask-Mail with SMTP
- **Scheduling**: Linux cron jobs
- **Deployment**: Gunicorn + Nginx

### User Roles

- **Admin**: Full system access and configuration
- **Manager**: Client and portfolio oversight
- **Advisor**: Client interaction and transaction management

---

## Installation & Setup

### Prerequisites

- Python 3.9+
- MySQL 8.0+
- Linux/Unix environment
- Required Python packages (see requirements.txt)

### Quick Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd app
   ```

2. **Install dependencies**
   ```bash
   pip3 install -r requirements.txt
   ```

3. **Configure database**
   ```bash
   # Update database configuration in main.py
   # Create database and run migrations
   flask db upgrade
   ```

4. **Configure email settings**
   ```bash
   # Update email configuration in main.py
   MAIL_SERVER = 'your-smtp-server'
   MAIL_PORT = 587
   MAIL_USERNAME = 'your-email@domain.com'
   MAIL_PASSWORD = 'your-password'
   ```

5. **Run the application**
   ```bash
   python3 main.py
   # Or using gunicorn
   gunicorn -w 4 -b 0.0.0.0:5000 main:app
   ```

---

## Cron Job Management

### Overview

The system includes automated cron jobs for daily reporting and system maintenance. These jobs run automatically and can also be triggered manually through the web interface.

### Available Cron Jobs

#### Daily Reports
1. **Daily Leads Report** (`daily_leads_report.py`)
   - **Schedule**: `30 0 * * *` (6:00 AM IST daily)
   - **Purpose**: Generates and sends daily leads report bucketed by status
   - **Recipients**: onboarding@equities4wealth.com, anshul@equities4wealth.com
   - **Log File**: `logs/daily_leads_report.log`

2. **Daily Monthly Investments Report** (`daily_monthly_investments_report.py`)
   - **Schedule**: `35 0 * * *` (6:05 AM IST daily)
   - **Purpose**: Generates and sends daily pending investments report
   - **Recipients**: onboarding@equities4wealth.com, anshul@equities4wealth.com
   - **Log File**: `logs/daily_monthly_investments_report.log`

3. **Daily Workflow Report** (`daily_workflow_report.py`)
   - **Schedule**: `0 9 * * *` (9:00 AM IST daily)
   - **Purpose**: Generates and sends daily workflow status report
   - **Recipients**: anshul@equities4wealth.com
   - **Log File**: `daily_report.log`

#### Monitoring Jobs
4. **Hourly SLA Check** (`manual_sla_check.py`)
   - **Schedule**: `0 * * * *` (Every hour)
   - **Purpose**: Checks for SLA violations and generates alerts
   - **Log File**: `alert_system.log`

5. **Daily Alert Report** (`alert_service.py`)
   - **Schedule**: `0 8 * * *` (8:00 AM IST daily)
   - **Purpose**: Generates daily alert summary report
   - **Log File**: `alert_report.log`

### Managing Cron Jobs

#### Web Interface
1. **Access**: Navigate to Settings → System Settings → Manage Cron Jobs
2. **View Status**: See current status of all cron jobs
3. **Test Run**: Click "Test Run" button to manually execute jobs
4. **Enable/Disable**: Toggle jobs on/off using the play/pause button
5. **Update Schedule**: Modify cron schedules through the web interface
6. **View Logs**: Access job logs for troubleshooting

#### Manual Management
```bash
# View current cron jobs
crontab -l

# Edit cron jobs
crontab -e

# Test a specific job
python3 daily_leads_report.py
```

### Cron Schedule Format

```
minute hour day month weekday
```

**Examples:**
- `0 9 * * *` - Daily at 9:00 AM
- `30 0 * * *` - Daily at 12:30 AM
- `0 * * * *` - Every hour
- `0 0 * * 0` - Weekly on Sunday at midnight

### Troubleshooting Cron Jobs

#### Common Issues
1. **Email Delivery Issues**
   - Check SMTP configuration
   - Verify recipient email addresses
   - Check spam folders

2. **Script Execution Errors**
   - Verify Python path and dependencies
   - Check file permissions
   - Review log files for errors

3. **Schedule Issues**
   - Verify cron schedule format
   - Check system timezone
   - Ensure cron service is running

#### Debugging Steps
1. **Check Logs**: Review job-specific log files
2. **Manual Testing**: Run scripts directly from command line
3. **Environment Check**: Verify Python environment and dependencies
4. **Permissions**: Ensure proper file and directory permissions

---

## Daily Reports System

### Overview

The Daily Reports System automatically generates and sends comprehensive reports via email. These reports provide insights into leads, investments, and workflow status.

### Report Types

#### Daily Leads Report
**Purpose**: Track lead status and progress
**Content**:
- Active vs inactive leads
- Leads by status category
- Lead source analysis
- Follow-up requirements

**Features**:
- HTML formatted reports
- Status bucketing
- Visual indicators
- Summary statistics

#### Daily Monthly Investments Report
**Purpose**: Monitor pending investments and workflow status
**Content**:
- Pending investment amounts
- Workflow stage distribution
- Client investment status
- Action items and deadlines

**Features**:
- Investment tracking
- Workflow progress
- Amount summaries
- Priority indicators

#### Daily Workflow Report
**Purpose**: Track workflow progress and bottlenecks
**Content**:
- Active workflows by stage
- Client investment amounts
- Workflow progress
- Action items and notes

**Features**:
- Stage-wise breakdown
- Amount tracking
- Progress indicators
- Detailed notes

### Email Configuration

**SMTP Settings**:
- Server: 65-254-81-55.cprapid.com
- Port: 587
- TLS: Enabled
- Authentication: Required

**Recipients**:
- Primary: anshul@equities4wealth.com
- Secondary: onboarding@equities4wealth.com

### Report Customization

#### Adding New Reports
1. Create new Python script in project root
2. Implement report generation logic
3. Add email sending functionality
4. Configure cron schedule
5. Update cron manager configuration

#### Modifying Existing Reports
1. Edit report generation functions
2. Update HTML templates
3. Modify email recipients
4. Test changes manually
5. Deploy to production

---

## Alert System

### Overview

The Alert System provides real-time monitoring and notifications for various system events, SLA violations, and important business processes.

### Alert Types

#### Lead Follow-up Alerts
- **Trigger**: Lead creation or status change
- **Purpose**: Ensure timely follow-up
- **SLA**: Configurable timeline (default: 24 hours)
- **Actions**: Email notifications, dashboard alerts

#### SLA Violation Alerts
- **Trigger**: Missed deadlines or SLA violations
- **Purpose**: Prevent process delays
- **Monitoring**: Hourly checks
- **Actions**: Escalation notifications

#### System Alerts
- **Trigger**: System errors or issues
- **Purpose**: Maintain system health
- **Monitoring**: Continuous
- **Actions**: Admin notifications

### Alert Configuration

#### Creating Alerts
```python
# Example alert creation
alert = Alert(
    alert_type='lead_followup',
    lead_id=lead.id,
    user_id=user.id,
    severity='warning',
    message='Lead requires follow-up',
    sla_timeline=24
)
```

#### Alert Severity Levels
- **Critical**: Immediate attention required
- **Warning**: Attention needed soon
- **Info**: Informational only

### Alert Management

#### Dashboard View
- Real-time alert display
- Filter by severity and type
- Acknowledge alerts
- View alert history

#### Email Notifications
- Automatic email alerts
- Configurable recipients
- HTML formatted messages
- Escalation rules

### SLA Monitoring

#### Lead Follow-up SLA
- **Timeline**: 24 hours from lead creation
- **Escalation**: Manager notification after 48 hours
- **Resolution**: Mark as acknowledged or resolved

#### Workflow SLA
- **Timeline**: Stage-specific deadlines
- **Monitoring**: Hourly checks
- **Actions**: Automatic escalation

---

## Workflow System

### Overview

The Workflow System automates investment processes from initial client contact to portfolio execution and updates.

### Workflow Stages

#### 1. FUNDS
- **Purpose**: Awaiting client funds
- **Actions**: Fund verification, amount confirmation
- **Timeline**: Client-dependent
- **Next Stage**: RECOS

#### 2. RECOS
- **Purpose**: Generating investment recommendations
- **Actions**: Portfolio analysis, recommendation generation
- **Timeline**: 1-2 business days
- **Next Stage**: NOTIFY

#### 3. NOTIFY
- **Purpose**: Client notification and approval
- **Actions**: Send recommendations, get client approval
- **Timeline**: 1-3 business days
- **Next Stage**: EXEC

#### 4. EXEC
- **Purpose**: Trade execution
- **Actions**: Execute trades, record transactions
- **Timeline**: Same day
- **Next Stage**: UPDATE

#### 5. UPDATE
- **Purpose**: Portfolio updates
- **Actions**: Update holdings, calculate performance
- **Timeline**: Same day
- **Next Stage**: COMPLETED

#### 6. COMPLETED
- **Purpose**: Workflow completion
- **Actions**: Final documentation, client communication
- **Timeline**: 1-2 business days

### Workflow Management

#### Creating Workflows
1. **Client Selection**: Choose client for workflow
2. **Investment Details**: Enter amount and preferences
3. **Model Assignment**: Assign asset allocation models
4. **Workflow Creation**: System creates workflow automatically

#### Tracking Progress
- **Dashboard View**: Real-time workflow status
- **Stage Tracking**: Monitor progress through stages
- **Action Items**: View required actions for each stage
- **Timeline**: Track time spent in each stage

#### Workflow Actions
- **Stage Transitions**: Move workflows between stages
- **Notes**: Add comments and observations
- **Documents**: Attach relevant documents
- **Notifications**: Alert stakeholders of changes

### Integration Features

#### Transaction Integration
- **Automatic Creation**: Transactions created during EXEC stage
- **Portfolio Updates**: Holdings updated automatically
- **Performance Tracking**: Real-time performance calculation

#### Reporting Integration
- **Daily Reports**: Workflow status included in daily reports
- **Performance Reports**: Workflow performance analysis
- **Client Reports**: Individual client workflow reports

---

## Process Flows

### Client Onboarding Process

1. **Lead Creation**
   - Capture lead information
   - Assign to advisor
   - Set follow-up timeline

2. **Initial Contact**
   - Advisor reaches out to lead
   - Qualify lead requirements
   - Schedule discovery meeting

3. **Discovery Meeting**
   - Understand investment goals
   - Assess risk tolerance
   - Determine investment amount

4. **Proposal Generation**
   - Create investment proposal
   - Assign asset allocation model
   - Generate recommendations

5. **Client Approval**
   - Present proposal to client
   - Address questions and concerns
   - Get client approval

6. **Account Setup**
   - Complete documentation
   - Set up investment account
   - Initiate fund transfer

7. **Investment Execution**
   - Execute approved trades
   - Update portfolio holdings
   - Monitor performance

### Investment Workflow Process

1. **Fund Receipt**
   - Verify fund transfer
   - Confirm investment amount
   - Update workflow status

2. **Portfolio Analysis**
   - Analyze current holdings
   - Generate recommendations
   - Prepare trade orders

3. **Client Communication**
   - Send recommendations
   - Get client approval
   - Address any concerns

4. **Trade Execution**
   - Execute approved trades
   - Record transactions
   - Update holdings

5. **Portfolio Update**
   - Calculate new values
   - Update performance metrics
   - Generate client report

6. **Follow-up**
   - Schedule review meeting
   - Monitor performance
   - Plan next steps

### Alert Management Process

1. **Alert Generation**
   - System detects trigger event
   - Creates alert record
   - Assigns to responsible user

2. **Alert Notification**
   - Send email notification
   - Display on dashboard
   - Update alert status

3. **Alert Response**
   - User acknowledges alert
   - Takes required action
   - Updates alert status

4. **Escalation**
   - Check SLA timeline
   - Escalate if needed
   - Notify management

5. **Resolution**
   - Mark alert as resolved
   - Document actions taken
   - Update process if needed

---

## Function Inventory

### Core System Functions

#### Authentication & Authorization
- `login()`: User authentication
- `logout()`: User logout
- `register()`: User registration
- `require_manager()`: Manager access control
- `require_advisor_or_manager()`: Advisor/Manager access control

#### Client Management
- `clients()`: List all clients
- `add_client()`: Create new client
- `client_details()`: View client details
- `edit_client()`: Update client information
- `delete_client()`: Remove client

#### Portfolio Management
- `portfolios()`: List all portfolios
- `create_portfolio()`: Create new portfolio
- `update_portfolio()`: Modify portfolio
- `delete_portfolio()`: Remove portfolio

#### Transaction Management
- `transactions()`: List all transactions
- `add_transaction()`: Create new transaction
- `edit_transaction()`: Update transaction
- `delete_transaction()`: Remove transaction

### Workflow Functions

#### Workflow Management
- `workflow_dashboard()`: Workflow overview
- `create_workflow()`: Start new workflow
- `update_workflow()`: Modify workflow
- `workflow_details()`: View workflow details

#### Workflow Actions
- `add_workflow_action()`: Record workflow action
- `update_workflow_stage()`: Change workflow stage
- `complete_workflow()`: Mark workflow complete

### Reporting Functions

#### Daily Reports
- `generate_daily_leads_report()`: Create leads report
- `generate_daily_investments_report()`: Create investments report
- `generate_daily_workflow_report()`: Create workflow report
- `send_email_report()`: Send reports via email

#### Performance Reports
- `calculate_portfolio_performance()`: Calculate performance metrics
- `generate_performance_report()`: Create performance report
- `calculate_xirr()`: Calculate internal rate of return

### Alert Functions

#### Alert Management
- `create_alert()`: Generate new alert
- `acknowledge_alert()`: Mark alert as acknowledged
- `resolve_alert()`: Mark alert as resolved
- `escalate_alert()`: Escalate alert to management

#### SLA Monitoring
- `check_sla_violations()`: Monitor SLA compliance
- `generate_sla_report()`: Create SLA report
- `send_sla_notifications()`: Send SLA notifications

### Utility Functions

#### Data Management
- `sync_portfolio_items()`: Sync portfolio data
- `recalculate_cashflow()`: Recalculate cashflow data
- `update_security_prices()`: Update security prices
- `validate_data()`: Validate data integrity

#### System Functions
- `backup_database()`: Create database backup
- `restore_database()`: Restore database from backup
- `cleanup_old_data()`: Remove old data
- `generate_system_report()`: Create system health report

---

## Deployment Guide

### Production Deployment

#### Server Requirements
- **OS**: Linux (Ubuntu 20.04+ recommended)
- **CPU**: 2+ cores
- **RAM**: 4GB+ minimum, 8GB+ recommended
- **Storage**: 50GB+ available space
- **Network**: Stable internet connection

#### Software Requirements
- **Python**: 3.9+
- **MySQL**: 8.0+
- **Nginx**: Latest stable version
- **Gunicorn**: Latest stable version
- **SSL Certificate**: Valid SSL certificate

### Deployment Steps

#### 1. Server Setup
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install python3 python3-pip mysql-server nginx git -y

# Create application user
sudo useradd -m -s /bin/bash inertia
sudo usermod -aG sudo inertia
```

#### 2. Application Setup
```bash
# Switch to application user
sudo su - inertia

# Clone repository
git clone <repository-url> /home/inertia/app
cd /home/inertia/app

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

#### 3. Database Setup
```bash
# Create database
sudo mysql -u root -p
CREATE DATABASE inertia_app2025;
CREATE USER 'inertia_admin'@'localhost' IDENTIFIED BY '!Nert!a2025$';
GRANT ALL PRIVILEGES ON inertia_app2025.* TO 'inertia_admin'@'localhost';
FLUSH PRIVILEGES;
EXIT;

# Run migrations
flask db upgrade
```

#### 4. Configuration
```bash
# Update configuration files
# - main.py: Database and email settings
# - gunicorn_config.py: Gunicorn settings
# - nginx.conf: Nginx configuration

# Set environment variables
export FLASK_ENV=production
export SECRET_KEY='your-secret-key'
```

#### 5. Service Setup
```bash
# Create systemd service
sudo nano /etc/systemd/system/inertia-app.service

[Unit]
Description=Inertia Investment Management System
After=network.target

[Service]
User=inertia
WorkingDirectory=/home/inertia/app
Environment=PATH=/home/inertia/app/venv/bin
ExecStart=/home/inertia/app/venv/bin/gunicorn -c gunicorn_config.py main:app
Restart=always

[Install]
WantedBy=multi-user.target

# Enable and start service
sudo systemctl enable inertia-app
sudo systemctl start inertia-app
```

#### 6. Nginx Configuration
```bash
# Create Nginx configuration
sudo nano /etc/nginx/sites-available/inertia-app

server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /path/to/certificate.crt;
    ssl_certificate_key /path/to/private.key;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# Enable site
sudo ln -s /etc/nginx/sites-available/inertia-app /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

#### 7. Cron Job Setup
```bash
# Set up cron jobs
crontab -e

# Add cron job entries
30 0 * * * cd /home/inertia/app && /home/inertia/app/venv/bin/python3 daily_leads_report.py
35 0 * * * cd /home/inertia/app && /home/inertia/app/venv/bin/python3 daily_monthly_investments_report.py
0 9 * * * cd /home/inertia/app && /home/inertia/app/venv/bin/python3 daily_workflow_report.py
0 * * * * cd /home/inertia/app && /home/inertia/app/venv/bin/python3 manual_sla_check.py
0 8 * * * cd /home/inertia/app && /home/inertia/app/venv/bin/python3 alert_service.py
```

### Monitoring & Maintenance

#### Log Monitoring
```bash
# Application logs
sudo journalctl -u inertia-app -f

# Nginx logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log

# Cron job logs
tail -f /home/inertia/app/logs/*.log
```

#### Backup Strategy
```bash
# Database backup
mysqldump -u inertia_admin -p inertia_app2025 > backup_$(date +%Y%m%d).sql

# Application backup
tar -czf app_backup_$(date +%Y%m%d).tar.gz /home/inertia/app

# Automated backup script
#!/bin/bash
BACKUP_DIR="/backup/inertia-app"
DATE=$(date +%Y%m%d_%H%M%S)

# Database backup
mysqldump -u inertia_admin -p'!Nert!a2025$' inertia_app2025 > $BACKUP_DIR/db_backup_$DATE.sql

# Application backup
tar -czf $BACKUP_DIR/app_backup_$DATE.tar.gz /home/inertia/app

# Clean old backups (keep 30 days)
find $BACKUP_DIR -name "*.sql" -mtime +30 -delete
find $BACKUP_DIR -name "*.tar.gz" -mtime +30 -delete
```

#### Performance Optimization
- **Database**: Regular optimization and indexing
- **Application**: Monitor memory usage and performance
- **Caching**: Implement Redis for session caching
- **CDN**: Use CDN for static assets

---

## Troubleshooting

### Common Issues

#### Email Delivery Problems
**Symptoms**: Reports not being received
**Causes**:
- SMTP configuration issues
- SSL certificate problems
- Rate limiting
- Network connectivity issues

**Solutions**:
1. Verify SMTP settings in main.py
2. Check SSL certificate configuration
3. Implement retry logic with delays
4. Test email functionality manually

#### Database Connection Issues
**Symptoms**: Application errors, data not loading
**Causes**:
- Database server down
- Connection pool exhaustion
- Incorrect credentials
- Network issues

**Solutions**:
1. Check database server status
2. Verify connection settings
3. Restart application
4. Check network connectivity

#### Cron Job Failures
**Symptoms**: Automated reports not running
**Causes**:
- Incorrect cron schedule
- Script execution errors
- Permission issues
- Environment problems

**Solutions**:
1. Verify cron schedule format
2. Test scripts manually
3. Check file permissions
4. Verify Python environment

#### Performance Issues
**Symptoms**: Slow application response
**Causes**:
- Database query optimization needed
- Memory leaks
- High server load
- Network latency

**Solutions**:
1. Optimize database queries
2. Monitor memory usage
3. Implement caching
4. Scale server resources

### Debugging Steps

#### 1. Check Logs
```bash
# Application logs
sudo journalctl -u inertia-app -f

# Error logs
tail -f /home/inertia/app/logs/error.log

# Access logs
tail -f /var/log/nginx/access.log
```

#### 2. Test Components
```bash
# Test database connection
python3 -c "from main import app; from extensions import db; app.app_context().push(); print('DB OK')"

# Test email functionality
curl http://localhost:5000/test-email

# Test cron jobs manually
python3 daily_leads_report.py
```

#### 3. Check System Resources
```bash
# Check disk space
df -h

# Check memory usage
free -h

# Check CPU usage
top

# Check network connectivity
ping google.com
```

### Emergency Procedures

#### Application Down
1. Check service status: `sudo systemctl status inertia-app`
2. Restart service: `sudo systemctl restart inertia-app`
3. Check logs for errors
4. Verify database connectivity
5. Check disk space and memory

#### Database Issues
1. Check MySQL status: `sudo systemctl status mysql`
2. Restart MySQL: `sudo systemctl restart mysql`
3. Check disk space
4. Verify backup availability
5. Consider database recovery if needed

#### Email System Down
1. Check SMTP server connectivity
2. Verify email credentials
3. Test with alternative SMTP server
4. Check SSL certificate validity
5. Implement fallback email service

### Support Contacts

#### Technical Support
- **Primary**: System Administrator
- **Backup**: Development Team
- **Emergency**: On-call Engineer

#### Escalation Process
1. **Level 1**: Basic troubleshooting
2. **Level 2**: Advanced debugging
3. **Level 3**: Vendor support
4. **Level 4**: Emergency response

---

## Conclusion

This comprehensive documentation provides a complete guide to the Inertia Investment Management System. The system is designed to be robust, scalable, and user-friendly, with comprehensive automation and reporting capabilities.

For additional support or questions, please refer to the troubleshooting section or contact the system administrator.

---

*Last Updated: August 22, 2025*
*Version: 1.0*
*System: Inertia Investment Management System*
