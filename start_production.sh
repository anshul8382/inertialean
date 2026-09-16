#!/bin/bash
#
# Production Startup Script for Inertia Investment App
# This script ensures the app is properly configured and running
#

set -e  # Exit on error

APP_DIR="/home/inertia/app"
SERVICE_NAME="inertia-app"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
VENV_PATH="${APP_DIR}/venv"
LOG_DIR="${APP_DIR}/logs"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} ✅ $1"
}

print_warning() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} ⚠️  $1"
}

print_error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} ❌ $1"
}

# Check if running as root or with sudo
if [ "$EUID" -eq 0 ]; then 
    print_warning "Running as root - this is OK for service setup"
fi

print_status "Starting Inertia Investment App Production Setup..."

# 1. Ensure logs directory exists
print_status "Creating logs directory..."
mkdir -p "$LOG_DIR"
chown -R inertia:inertia "$LOG_DIR" 2>/dev/null || true
chmod 755 "$LOG_DIR"

# 2. Check virtual environment
if [ ! -d "$VENV_PATH" ]; then
    print_error "Virtual environment not found at $VENV_PATH"
    exit 1
fi

# 3. Check if Gunicorn is installed
print_status "Checking Gunicorn installation..."
if ! "$VENV_PATH/bin/python" -c "import gunicorn" 2>/dev/null; then
    print_warning "Gunicorn not found, installing..."
    "$VENV_PATH/bin/pip" install gunicorn
fi

# 4. Install/Update systemd service
print_status "Setting up systemd service..."
if [ -f "${APP_DIR}/config/${SERVICE_NAME}.service" ]; then
    print_status "Copying systemd service file..."
    sudo cp "${APP_DIR}/config/${SERVICE_NAME}.service" "$SERVICE_FILE"
    sudo systemctl daemon-reload
    print_success "Systemd service updated"
else
    print_warning "Service file not found at ${APP_DIR}/config/${SERVICE_NAME}.service"
fi

# 5. Check database connectivity
print_status "Checking database connectivity..."
if "$VENV_PATH/bin/python" -c "from extensions import db; from main import create_app; from config import ProductionConfig; app = create_app(ProductionConfig); app.app_context().push(); db.session.execute(db.text('SELECT 1'))" 2>/dev/null; then
    print_success "Database connection successful"
else
    print_error "Database connection failed - check your configuration"
    exit 1
fi

# 6. Start/restart the service
print_status "Managing systemd service..."
if systemctl is-active --quiet "$SERVICE_NAME"; then
    print_status "Service is running, restarting..."
    sudo systemctl restart "$SERVICE_NAME"
else
    print_status "Starting service..."
    sudo systemctl start "$SERVICE_NAME"
    sudo systemctl enable "$SERVICE_NAME"
fi

# Wait a moment for startup
sleep 3

# 7. Check service status
if systemctl is-active --quiet "$SERVICE_NAME"; then
    print_success "Service is running"
    
    # Check health endpoint
    print_status "Checking application health..."
    sleep 2
    if curl -s -f http://localhost:8000/health > /dev/null 2>&1; then
        print_success "Application health check passed"
    else
        print_warning "Health check failed - service may still be starting"
    fi
    
    # Show status
    echo ""
    print_status "Service Status:"
    sudo systemctl status "$SERVICE_NAME" --no-pager -l
    
    echo ""
    print_success "Production setup complete!"
    print_status "Useful commands:"
    echo "  - Check status: sudo systemctl status $SERVICE_NAME"
    echo "  - View logs: sudo journalctl -u $SERVICE_NAME -f"
    echo "  - Restart: sudo systemctl restart $SERVICE_NAME"
    echo "  - Stop: sudo systemctl stop $SERVICE_NAME"
    echo "  - Health check: curl http://localhost:8000/health"
    
else
    print_error "Service failed to start"
    print_status "Checking logs..."
    sudo journalctl -u "$SERVICE_NAME" -n 50 --no-pager
    exit 1
fi


