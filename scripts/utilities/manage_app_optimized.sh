#!/bin/bash

# Optimized Application Management Script
# This script provides better management of the Inertia Investment App

APP_DIR="/home/inertia/app"
PID_FILE="$APP_DIR/gunicorn.pid"
LOG_DIR="$APP_DIR/logs"
CONFIG_FILE="$APP_DIR/gunicorn_config.py"
PROD_CONFIG_FILE="$APP_DIR/gunicorn_config_production.py"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

print_error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

# Function to check if app is running
is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# Function to check app health
check_health() {
    print_status "Checking application health..."
    
    if ! is_running; then
        print_error "Application is not running"
        return 1
    fi
    
    # Test basic connectivity
    local response=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/ 2>/dev/null)
    
    if [ "$response" = "302" ] || [ "$response" = "200" ]; then
        print_success "Application is healthy (HTTP $response)"
        return 0
    else
        print_error "Application health check failed (HTTP $response)"
        return 1
    fi
}

# Function to start the application
start_app() {
    print_status "Starting Inertia Investment App..."
    
    if is_running; then
        print_warning "Application is already running"
        return 0
    fi
    
    cd "$APP_DIR"
    
    # Activate virtual environment
    source venv/bin/activate
    
    # Start with optimized configuration
    if [ "$1" = "production" ]; then
        print_status "Starting in production mode..."
        gunicorn -c "$PROD_CONFIG_FILE" main:app
    else
        print_status "Starting in development mode..."
        gunicorn -c "$CONFIG_FILE" main:app
    fi
    
    # Wait a moment for startup
    sleep 3
    
    if is_running; then
        print_success "Application started successfully"
        check_health
    else
        print_error "Failed to start application"
        return 1
    fi
}

# Function to stop the application
stop_app() {
    print_status "Stopping Inertia Investment App..."
    
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            kill -TERM "$pid"
            
            # Wait for graceful shutdown
            local count=0
            while ps -p "$pid" > /dev/null 2>&1 && [ $count -lt 30 ]; do
                sleep 1
                count=$((count + 1))
            done
            
            # Force kill if still running
            if ps -p "$pid" > /dev/null 2>&1; then
                print_warning "Force killing process..."
                kill -KILL "$pid"
            fi
            
            print_success "Application stopped"
        else
            print_warning "Application was not running"
        fi
    else
        print_warning "PID file not found"
    fi
    
    # Clean up any remaining gunicorn processes
    pkill -f gunicorn 2>/dev/null || true
}

# Function to restart the application
restart_app() {
    print_status "Restarting Inertia Investment App..."
    stop_app
    sleep 2
    start_app "$1"
}

# Function to show status
show_status() {
    print_status "Application Status:"
    echo "===================="
    
    if is_running; then
        local pid=$(cat "$PID_FILE")
        print_success "Status: Running (PID: $pid)"
        
        # Show worker processes
        echo "Workers:"
        ps aux | grep "gunicorn.*worker" | grep -v grep | while read line; do
            echo "  $line"
        done
        
        # Show memory usage
        echo "Memory Usage:"
        ps aux | grep "gunicorn.*worker" | grep -v grep | awk '{sum+=$6} END {print "  Total RSS: " sum/1024 " MB"}'
        
        # Health check
        check_health
        
    else
        print_error "Status: Not running"
    fi
    
    echo ""
    echo "System Resources:"
    echo "================="
    free -h | grep -E "Mem|Swap"
    echo ""
    echo "Disk Usage:"
    df -h "$APP_DIR" | tail -1
}

# Function to show logs
show_logs() {
    local lines=${1:-50}
    print_status "Showing last $lines lines of error log..."
    echo "===================="
    tail -n "$lines" "$LOG_DIR/gunicorn_error.log"
}

# Function to monitor the application
monitor_app() {
    print_status "Starting application monitoring..."
    echo "Press Ctrl+C to stop monitoring"
    echo ""
    
    while true; do
        if check_health; then
            echo -e "${GREEN}✓${NC} $(date '+%H:%M:%S') - App is healthy"
        else
            echo -e "${RED}✗${NC} $(date '+%H:%M:%S') - App is unhealthy"
            print_warning "Consider restarting the application"
        fi
        
        sleep 30
    done
}

# Function to clean up logs
clean_logs() {
    print_status "Cleaning up old logs..."
    
    # Keep only last 7 days of logs
    find "$LOG_DIR" -name "*.log" -mtime +7 -delete 2>/dev/null || true
    
    # Compress old logs
    find "$LOG_DIR" -name "*.log" -mtime +1 -exec gzip {} \; 2>/dev/null || true
    
    print_success "Log cleanup completed"
}

# Main script logic
case "$1" in
    start)
        start_app "$2"
        ;;
    stop)
        stop_app
        ;;
    restart)
        restart_app "$2"
        ;;
    status)
        show_status
        ;;
    health)
        check_health
        ;;
    logs)
        show_logs "$2"
        ;;
    monitor)
        monitor_app
        ;;
    clean)
        clean_logs
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|health|logs|monitor|clean}"
        echo ""
        echo "Commands:"
        echo "  start [production]  - Start the application (optionally in production mode)"
        echo "  stop               - Stop the application"
        echo "  restart [production] - Restart the application"
        echo "  status             - Show application status and system resources"
        echo "  health             - Check application health"
        echo "  logs [lines]       - Show recent error logs (default: 50 lines)"
        echo "  monitor            - Monitor application health continuously"
        echo "  clean              - Clean up old log files"
        echo ""
        echo "Examples:"
        echo "  $0 start production"
        echo "  $0 status"
        echo "  $0 logs 100"
        echo "  $0 monitor"
        exit 1
        ;;
esac
