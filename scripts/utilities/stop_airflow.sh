#!/bin/bash

# Airflow stop script
export AIRFLOW_HOME=/home/inertia/app/airflow

echo "Stopping Airflow services..."

# Stop the API server (replaces webserver)
echo "Stopping Airflow API server..."
pkill -f "airflow api-server"

# Stop the scheduler
echo "Stopping Airflow scheduler..."
pkill -f "airflow scheduler"

echo "Airflow services stopped!"
