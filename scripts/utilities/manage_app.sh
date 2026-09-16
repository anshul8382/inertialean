#!/bin/bash

# App management script for single-user testing
APP_DIR="/home/inertia/app"
PID_FILE="$APP_DIR/gunicorn.pid"
SINGLE_PID_FILE="$APP_DIR/gunicorn_single.pid"

echo "=== Flask App Manager ==="

case "$1" in
    stop)
        echo "Stopping gunicorn processes..."
        pkill -f "gunicorn.*main:app"
        if [ -f "$PID_FILE" ]; then
            rm -f "$PID_FILE"
        fi
        if [ -f "$SINGLE_PID_FILE" ]; then
            rm -f "$SINGLE_PID_FILE"
        fi
        echo "Gunicorn stopped."
        ;;
    start)
        echo "Starting Flask app with single worker..."
        cd "$APP_DIR"
        source venv/bin/activate
        gunicorn -c gunicorn_config_single.py main:app
        ;;
    start-multi)
        echo "Starting Flask app with multiple workers..."
        cd "$APP_DIR"
        source venv/bin/activate
        gunicorn -c gunicorn_config.py main:app
        ;;
    restart)
        echo "Restarting app..."
        $0 stop
        sleep 2
        $0 start
        ;;
    restart-multi)
        echo "Restarting app with multiple workers..."
        $0 stop
        sleep 2
        $0 start-multi
        ;;
    status)
        if pgrep -f "gunicorn.*main:app" > /dev/null; then
            echo "Gunicorn is running"
            ps aux | grep "gunicorn.*main:app" | grep -v grep
        else
            echo "No Flask app is running"
        fi
        ;;
    reload)
        echo "Reloading gunicorn (graceful restart)..."
        if [ -f "$PID_FILE" ]; then
            kill -HUP $(cat "$PID_FILE")
        elif [ -f "$SINGLE_PID_FILE" ]; then
            kill -HUP $(cat "$SINGLE_PID_FILE")
        else
            echo "No PID file found. Use restart instead."
        fi
        ;;
    *)
        echo "Usage: $0 {start|start-multi|stop|restart|restart-multi|status|reload}"
        echo ""
        echo "Commands:"
        echo "  start       - Start Flask app with single worker (for testing)"
        echo "  start-multi - Start Flask app with multiple workers (production)"
        echo "  stop        - Stop all Flask/gunicorn processes"
        echo "  restart     - Restart with single worker"
        echo "  restart-multi - Restart with multiple workers"
        echo "  status      - Check current app status"
        echo "  reload      - Graceful reload (if PID file exists)"
        exit 1
        ;;
esac
