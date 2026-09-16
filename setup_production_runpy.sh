#!/bin/bash
#
# Setup Production Service for run.py
# This sets up systemd to run your app using run.py with auto-restart
#

set -e

APP_DIR="/home/inertia/app"
SERVICE_NAME="inertia-app-runpy"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SOURCE_SERVICE="${APP_DIR}/config/${SERVICE_NAME}.service"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

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

print_status "Setting up production service for run.py..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    print_error "This script must be run as root or with sudo"
    exit 1
fi

# 1. Copy service file
if [ ! -f "$SOURCE_SERVICE" ]; then
    print_error "Service file not found: $SOURCE_SERVICE"
    exit 1
fi

print_status "Installing systemd service..."
cp "$SOURCE_SERVICE" "$SERVICE_FILE"
print_success "Service file installed"

# 2. Reload systemd
print_status "Reloading systemd daemon..."
systemctl daemon-reload
print_success "Systemd daemon reloaded"

# 3. Enable service (start on boot)
print_status "Enabling service (auto-start on boot)..."
systemctl enable "$SERVICE_NAME"
print_success "Service enabled"

# 4. Start service
print_status "Starting service..."
systemctl start "$SERVICE_NAME"
sleep 3

# 5. Check status
if systemctl is-active --quiet "$SERVICE_NAME"; then
    print_success "Service is running!"
    
    print_status "Service Status:"
    systemctl status "$SERVICE_NAME" --no-pager -l
    
    echo ""
    print_success "Setup complete!"
    echo ""
    print_status "Useful commands:"
    echo "  - Check status: sudo systemctl status $SERVICE_NAME"
    echo "  - View logs: sudo journalctl -u $SERVICE_NAME -f"
    echo "  - Restart: sudo systemctl restart $SERVICE_NAME"
    echo "  - Stop: sudo systemctl stop $SERVICE_NAME"
    echo "  - Disable auto-start: sudo systemctl disable $SERVICE_NAME"
    echo ""
    print_status "The app will automatically restart if it crashes!"
    
else
    print_error "Service failed to start"
    print_status "Checking logs..."
    journalctl -u "$SERVICE_NAME" -n 50 --no-pager
    exit 1
fi


