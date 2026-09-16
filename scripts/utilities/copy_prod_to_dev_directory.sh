#!/bin/bash

# Script to copy production directory to development directory
echo "Copying production directory to development directory..."

# Define paths
PROD_DIR="/home/inertia/app"
DEV_DIR="/home/inertia/app_dev"

# Create backup of existing dev directory if it exists
if [ -d "$DEV_DIR" ]; then
    echo "Creating backup of existing development directory..."
    BACKUP_DIR="/home/inertia/app_dev_backup_$(date +%Y%m%d_%H%M%S)"
    mv "$DEV_DIR" "$BACKUP_DIR"
    echo "Backup created: $BACKUP_DIR"
fi

# Copy the entire production directory to development
echo "Copying production directory to development..."
cp -r "$PROD_DIR" "$DEV_DIR"

# Remove unnecessary files from development copy
echo "Cleaning up development directory..."
cd "$DEV_DIR"

# Remove git directory (we'll initialize a new one)
rm -rf .git

# Remove log files
rm -f *.log
rm -rf logs/*

# Remove PID files
rm -f *.pid

# Remove cache files
rm -rf __pycache__/
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null

# Remove temporary files
rm -f /tmp/*.sql

# Update configuration for development
echo "Updating configuration for development environment..."
sed -i 's/inertia_app2025/inertia_app2025_dev/g' config.py

# Initialize new git repository for development
echo "Initializing new git repository for development..."
git init
git add .
git commit -m "Initial development environment setup"

echo "✅ Development environment created successfully!"
echo "Development directory: $DEV_DIR"
echo ""
echo "To start development:"
echo "cd $DEV_DIR"
echo "./start_development.sh"
echo ""
echo "To start production:"
echo "cd $PROD_DIR"
echo "./start_production.sh"
