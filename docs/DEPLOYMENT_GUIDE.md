# Deployment Guide: Dev to Production

## Overview
This guide explains how to safely move changes from the development environment to production.

## Environment Setup
- **Production**: `http://65.254.81.55:5000` (Database: `inertia_app2025`)
- **Development**: `http://65.254.81.55:5001` (Database: `inertia_app2025_dev`)

## Migration Methods

### Method 1: Using Migration Scripts (Recommended)

#### File Migration
```bash
# Copy specific file
./migrate_dev_to_prod.sh main.py

# Copy specific directory
./migrate_dev_to_prod.sh routes/

# Copy all Python files
./migrate_dev_to_prod.sh *.py

# Copy templates
./migrate_dev_to_prod.sh templates/

# Copy static files
./migrate_dev_to_prod.sh static/

# Copy everything (BE CAREFUL!)
./migrate_dev_to_prod.sh --all
```

#### Database Migration
```bash
# Safe: Only schema changes (table structure)
./migrate_database.sh --schema-only

# Safe: Only data (content, no structure changes)
./migrate_database.sh --data-only

# Dangerous: Full sync (overwrites production data)
./migrate_database.sh --full

# Just create backups
./migrate_database.sh --backup-only
```

### Method 2: Manual Copy (For small changes)

#### Copy Single File
```bash
# Backup production file
cp /home/inertia/app/main.py /home/inertia/backups/main.py.backup

# Copy from dev to prod
cp /home/inertia/app_dev/main.py /home/inertia/app/main.py
```

#### Copy Directory
```bash
# Backup production directory
tar -czf /home/inertia/backups/routes_backup.tar.gz -C /home/inertia/app routes/

# Copy from dev to prod
cp -r /home/inertia/app_dev/routes/ /home/inertia/app/
```

### Method 3: Git-Based Deployment (Advanced)

```bash
# In dev directory
cd /home/inertia/app_dev
git add .
git commit -m "Feature: Add new functionality"
git push origin main

# In production directory
cd /home/inertia/app
git pull origin main
```

## Step-by-Step Deployment Process

### 1. Test in Development
```bash
cd /home/inertia/app_dev
source venv/bin/activate
python main.py
# Test at http://65.254.81.55:5001
```

### 2. Create Backup
```bash
# Always backup before deployment
./migrate_database.sh --backup-only
```

### 3. Deploy Code Changes
```bash
# For specific files
./migrate_dev_to_prod.sh main.py routes/ templates/

# For everything
./migrate_dev_to_prod.sh --all
```

### 4. Deploy Database Changes (if needed)
```bash
# For schema changes only
./migrate_database.sh --schema-only
```

### 5. Restart Production Service
```bash
sudo systemctl restart inertia-flask.service
```

### 6. Verify Deployment
- Check production logs: `sudo journalctl -u inertia-flask.service -f`
- Test production site: `http://65.254.81.55:5000`
- Verify functionality works as expected

## Best Practices

### Before Deployment
1. ✅ Test thoroughly in development environment
2. ✅ Create database backup
3. ✅ Review changes
4. ✅ Plan rollback strategy

### During Deployment
1. ✅ Deploy during low-traffic hours
2. ✅ Monitor logs during deployment
3. ✅ Test critical functionality immediately
4. ✅ Have rollback plan ready

### After Deployment
1. ✅ Monitor application performance
2. ✅ Check error logs
3. ✅ Verify all features work
4. ✅ Update documentation if needed

## Rollback Procedure

### Code Rollback
```bash
# Restore from backup
cp /home/inertia/backups/backup_YYYYMMDD_HHMMSS_main.py /home/inertia/app/main.py

# Restart service
sudo systemctl restart inertia-flask.service
```

### Database Rollback
```bash
# Restore database from backup
mysql -u inertia_admin -p'!Nert!a2025$' inertia_app2025 < /home/inertia/backups/backup_inertia_app2025_YYYYMMDD_HHMMSS.sql
```

## Common Scenarios

### Scenario 1: Adding New Feature
1. Develop in `/home/inertia/app_dev/`
2. Test thoroughly on port 5001
3. Deploy: `./migrate_dev_to_prod.sh routes/ templates/`
4. Restart: `sudo systemctl restart inertia-flask.service`

### Scenario 2: Database Schema Changes
1. Test schema changes in dev database
2. Backup production: `./migrate_database.sh --backup-only`
3. Deploy schema: `./migrate_database.sh --schema-only`
4. Deploy code changes
5. Restart service

### Scenario 3: Bug Fix
1. Fix bug in dev environment
2. Test fix thoroughly
3. Deploy specific files: `./migrate_dev_to_prod.sh main.py routes/bug_fix.py`
4. Restart service

## Troubleshooting

### Service Won't Start
```bash
# Check service status
sudo systemctl status inertia-flask.service

# Check logs
sudo journalctl -u inertia-flask.service -f

# Check port conflicts
netstat -tlnp | grep :5000
```

### Database Connection Issues
```bash
# Test database connection
mysql -u inertia_admin -p'!Nert!a2025$' -e "USE inertia_app2025; SHOW TABLES;"

# Check database permissions
mysql -u root -e "SHOW GRANTS FOR 'inertia_admin'@'localhost';"
```

### Template Errors
```bash
# Check template syntax
cd /home/inertia/app
python -c "from flask import Flask; app = Flask(__name__); app.template_folder='templates'"
```

## Emergency Contacts
- **Server Access**: SSH to 65.254.81.55
- **Database**: MySQL on localhost:3306
- **Service**: systemd service `inertia-flask.service`
- **Logs**: `/var/log/` and `sudo journalctl -u inertia-flask.service` 