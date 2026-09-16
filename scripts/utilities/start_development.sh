#!/bin/bash

# Development startup script for Inertia Investment App
echo "Starting Inertia Investment App in development mode..."

# Set environment
export FLASK_ENV=development

# Kill any existing processes
pkill -f "gunicorn"
pkill -f "python3 run_app.py"

# Wait a moment for processes to stop
sleep 2

# Start with Flask development server
echo "Starting Flask development server..."
python3 run_app.py
