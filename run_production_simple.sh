#!/bin/bash
#
# Simple Production Runner - Keeps run.py running with auto-restart
# This uses Flask's built-in server but wraps it to auto-restart on crashes
#

APP_DIR="/home/inertia/app"
LOG_FILE="${APP_DIR}/logs/app.log"
PID_FILE="${APP_DIR}/app.pid"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

cd "$APP_DIR"

# Create logs directory
mkdir -p "$(dirname "$LOG_FILE")"

# Function to start the app
start_app() {
    echo -e "${GREEN}Starting Inertia Investment App...${NC}"
    echo "Logs: $LOG_FILE"
    echo "PID: $$"
    echo $$ > "$PID_FILE"
    
    # Set environment
    export FLASK_ENV=production
    
    # Run with auto-restart on failure
    while true; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting application..." | tee -a "$LOG_FILE"
        
        # Run the app
        python3 run.py 2>&1 | tee -a "$LOG_FILE"
        
        EXIT_CODE=$?
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Application exited with code $EXIT_CODE" | tee -a "$LOG_FILE"
        
        # If it was a clean exit (Ctrl+C), don't restart
        if [ $EXIT_CODE -eq 130 ] || [ $EXIT_CODE -eq 0 ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Clean exit, stopping..." | tee -a "$LOG_FILE"
            break
        fi
        
        # Otherwise, restart after a delay
        echo -e "${YELLOW}Application crashed, restarting in 5 seconds...${NC}"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Restarting in 5 seconds..." | tee -a "$LOG_FILE"
        sleep 5
    done
    
    # Clean up
    rm -f "$PID_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Application stopped" | tee -a "$LOG_FILE"
}

# Function to stop the app
stop_app() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo -e "${YELLOW}Stopping application (PID: $PID)...${NC}"
            kill -TERM "$PID" 2>/dev/null
            sleep 2
            if ps -p "$PID" > /dev/null 2>&1; then
                echo -e "${RED}Force killing...${NC}"
                kill -9 "$PID" 2>/dev/null
            fi
        fi
        rm -f "$PID_FILE"
        echo -e "${GREEN}Application stopped${NC}"
    else
        echo -e "${YELLOW}Application is not running${NC}"
    fi
}

# Handle signals
trap 'stop_app; exit' SIGTERM SIGINT

# Main logic
case "${1:-start}" in
    start)
        start_app
        ;;
    stop)
        stop_app
        ;;
    restart)
        stop_app
        sleep 2
        start_app
        ;;
    status)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p "$PID" > /dev/null 2>&1; then
                echo -e "${GREEN}Application is running (PID: $PID)${NC}"
            else
                echo -e "${RED}PID file exists but process is not running${NC}"
                rm -f "$PID_FILE"
            fi
        else
            echo -e "${YELLOW}Application is not running${NC}"
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac


