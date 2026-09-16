#!/bin/bash

# Airflow startup script
export AIRFLOW_HOME=/home/inertia/app/airflow

echo "Starting Airflow services..."

# Start the API server (replaces webserver) in background
echo "Starting Airflow API server..."
airflow api-server --port 8080 --daemon

# Start the scheduler in background
echo "Starting Airflow scheduler..."
airflow scheduler --daemon

echo "Airflow services started!"
echo "Web UI available at: http://localhost:8080"
echo "Username: admin"
echo "Password: admin123"
echo ""
echo "To stop Airflow: ./stop_airflow.sh"
echo "To view logs: tail -f /home/inertia/app/airflow/logs/scheduler/latest/*.log"
