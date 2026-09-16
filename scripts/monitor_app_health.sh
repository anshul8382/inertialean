#!/bin/bash
#
# Application Health Monitor
# This script checks if the app is healthy and restarts if needed
# Can be run as a cron job (e.g., every 5 minutes)
#

APP_DIR="/home/inertia/app"
SERVICE_NAME="inertia-app"
HEALTH_URL="http://localhost:8000/health"
LOG_FILE="${APP_DIR}/logs/health_monitor.log"

# Create log file if it doesn't exist
mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Check if service is running
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    log_message "ERROR: Service $SERVICE_NAME is not running. Attempting to start..."
    sudo systemctl start "$SERVICE_NAME"
    sleep 5
    
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_message "SUCCESS: Service started successfully"
    else
        log_message "ERROR: Failed to start service"
        exit 1
    fi
fi

# Check health endpoint
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")

if [ "$HTTP_CODE" = "200" ]; then
    log_message "INFO: Application is healthy (HTTP $HTTP_CODE)"
    exit 0
elif [ "$HTTP_CODE" = "503" ]; then
    log_message "WARNING: Application health check returned 503 (unhealthy)"
    log_message "INFO: Restarting service..."
    sudo systemctl restart "$SERVICE_NAME"
    sleep 5
    
    # Check again after restart
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        log_message "SUCCESS: Application recovered after restart"
    else
        log_message "ERROR: Application still unhealthy after restart (HTTP $HTTP_CODE)"
        exit 1
    fi
elif [ "$HTTP_CODE" = "000" ]; then
    log_message "ERROR: Cannot reach health endpoint (connection failed)"
    log_message "INFO: Restarting service..."
    sudo systemctl restart "$SERVICE_NAME"
    sleep 5
    
    # Check again
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        log_message "SUCCESS: Application recovered after restart"
    else
        log_message "ERROR: Application still unreachable after restart"
        exit 1
    fi
else
    log_message "WARNING: Unexpected HTTP code: $HTTP_CODE"
    exit 1
fi


