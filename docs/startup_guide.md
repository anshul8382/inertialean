# Inertia Investment App - Environment Startup Guide

## 🚀 Port Configuration

| Environment | Port | URL | Purpose |
|-------------|------|-----|---------|
| **Production** | 5000 | http://your-domain:5000 | Main production application |
| **Development** | 5001 | http://your-domain:5001 | Development application |
| **Test** | 5002 | http://localhost:5002 | Testing and QA environment |
| **Production Airflow** | 8080 | http://your-domain:8080 | Airflow dashboard |

## 📊 Production Environment

### Directory: `/home/inertia/app`
### Database: `inertia_app2025`

### Start Production:
```bash
cd /home/inertia/app
./start_production.sh
```

### Stop Production:
```bash
cd /home/inertia/app
pkill -f "gunicorn"
```

## 🔧 Development Environment

### Directory: `/home/inertia/app_dev`
### Database: `inertia_app2025_dev`

### Start Development:
```bash
cd /home/inertia/app_dev
./start_development.sh
```

### Stop Development:
```bash
pkill -f "python3 run_app.py"
```

## 🧪 Test Environment

### Directory: `/home/inertia/app`
### Database: `inertia_app2025_test`

### Setup Test Database:
```bash
cd /home/inertia/app
./scripts/utilities/setup_test_database.sh
export FLASK_ENV=test
flask db upgrade
```

### Start Test:
```bash
cd /home/inertia/app
./start_test.sh
```

### Stop Test:
```bash
pkill -f "flask run.*--port 5002"
```

**Note**: For detailed test environment setup, see [TEST_ENVIRONMENT_SETUP.md](./TEST_ENVIRONMENT_SETUP.md)

## 🎯 Quick Commands

### Check Environment Status:
```bash
cd /home/inertia/app
./check_environments.sh
```

### Start All Environments:
```bash
# Terminal 1 - Production
cd /home/inertia/app && ./start_production.sh

# Terminal 2 - Development  
cd /home/inertia/app_dev && ./start_development.sh

# Terminal 3 - Test
cd /home/inertia/app && ./start_test.sh
```

### Access Applications:
- **Production**: http://your-domain:5000
- **Development**: http://your-domain:5001
- **Test**: http://localhost:5002
- **Airflow**: http://your-domain:8080

## 🔄 Database Sync

### Copy Production to Development Database:
```bash
cd /home/inertia/app
./copy_prod_to_dev_fixed.sh
```

### Copy Production Directory to Development:
```bash
cd /home/inertia/app
./copy_prod_to_dev_directory.sh
```

## 📝 Important Notes

1. **Development** uses port **5001** to avoid conflicts with production
2. **Test** uses port **5002** to avoid conflicts with production and development
3. **Development** connects to `inertia_app2025_dev` database
4. **Test** connects to `inertia_app2025_test` database
5. **Production** connects to `inertia_app2025` database
6. All environments can run simultaneously
7. Development and Test have debug mode enabled for easier troubleshooting

## 🛠️ Troubleshooting

### If ports are already in use:
```bash
# Check what's using the ports
netstat -tulpn | grep :5000
netstat -tulpn | grep :5001
netstat -tulpn | grep :5002
netstat -tulpn | grep :8080

# Kill processes if needed
pkill -f "gunicorn"
pkill -f "python3 run_app.py"
pkill -f "flask run.*--port 5002"
```

### If databases are not accessible:
```bash
# Check database connectivity
mysql -u inertia_admin -p'!Nert!a2025$' -e "SHOW DATABASES;" | grep inertia
```
