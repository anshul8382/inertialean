#!/bin/bash

# Production startup script for Inertia Investment App
echo "Starting Inertia Investment App in production mode..."

# Create logs directory if it doesn't exist
mkdir -p logs

# Set environment
export FLASK_ENV=production

# Kill any existing processes
pkill -f "gunicorn"
pkill -f "python3 run_app.py"

# Wait a moment for processes to stop
sleep 2

# Start with Gunicorn for production
echo "Starting Gunicorn server..."
gunicorn -c gunicorn_config.py main:app

# Alternative: Start with Flask development server (not recommended for production)
# echo "Starting Flask development server..."
# python3 run_app.py
