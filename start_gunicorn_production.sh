#!/bin/bash
#
# Production Gunicorn Startup Script for Inertia Investment App
# This script ensures proper startup with auto-restart capabilities
#

set -e  # Exit on error

APP_DIR="/home/inertia/app"
VENV_PATH="${APP_DIR}/venv"
LOG_DIR="${APP_DIR}/logs"
PIDFILE="${APP_DIR}/gunicorn.pid"
CONFIG_FILE="${APP_DIR}/config/gunicorn_config_production.py"

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

# Check if running as correct user
if [ "$USER" != "inertia" ]; then
    print_warning "Not running as 'inertia' user. Current user: $USER"
fi

print_status "Starting Inertia Investment App with Gunicorn..."

# 1. Ensure logs directory exists
print_status "Creating logs directory..."
mkdir -p "$LOG_DIR"
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

# 4. Check database connectivity
print_status "Checking database connectivity..."
if "$VENV_PATH/bin/python" -c "from extensions import db; from main import create_app; from config import ProductionConfig; app = create_app(ProductionConfig); app.app_context().push(); db.session.execute(db.text('SELECT 1'))" 2>/dev/null; then
    print_success "Database connection successful"
else
    print_error "Database connection failed - check your configuration"
    exit 1
fi

# 5. Stop any existing Gunicorn processes
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        print_status "Stopping existing Gunicorn process (PID: $OLD_PID)..."
        kill -TERM "$OLD_PID" 2>/dev/null || true
        sleep 2
        # Force kill if still running
        if ps -p "$OLD_PID" > /dev/null 2>&1; then
            kill -9 "$OLD_PID" 2>/dev/null || true
        fi
    fi
    rm -f "$PIDFILE"
fi

# Also kill any other gunicorn processes
pkill -f "gunicorn.*wsgi:app" 2>/dev/null || true
sleep 2

# 6. Start Gunicorn
print_status "Starting Gunicorn server..."
cd "$APP_DIR"

"$VENV_PATH/bin/gunicorn" \
    --config "$CONFIG_FILE" \
    --pid "$PIDFILE" \
    wsgi:app

# Wait a moment
sleep 3

# 7. Check if Gunicorn started successfully
if [ -f "$PIDFILE" ] && ps -p $(cat "$PIDFILE") > /dev/null 2>&1; then
    print_success "Gunicorn started successfully (PID: $(cat $PIDFILE))"
    print_status "Application is running on http://0.0.0.0:5000"
    print_status "Logs are available at:"
    echo "  - Access: $LOG_DIR/gunicorn_access.log"
    echo "  - Error: $LOG_DIR/gunicorn_error.log"
else
    print_error "Gunicorn failed to start"
    print_status "Check logs for details:"
    echo "  tail -f $LOG_DIR/gunicorn_error.log"
    exit 1
fi




