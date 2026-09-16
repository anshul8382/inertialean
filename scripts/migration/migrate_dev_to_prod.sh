#!/bin/bash

# Dev to Production Migration Script
# Usage: ./migrate_dev_to_prod.sh [file_or_directory]

set -e  # Exit on any error

# Configuration
DEV_DIR="/home/inertia/app_dev"
PROD_DIR="/home/inertia/app"
BACKUP_DIR="/home/inertia/backups"
DATE=$(date +%Y%m%d_%H%M%S)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Dev to Production Migration Script ===${NC}"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Function to create backup
create_backup() {
    local target="$1"
    local backup_name="backup_${DATE}_$(basename "$target")"
    
    echo -e "${YELLOW}Creating backup of $target...${NC}"
    if [ -d "$target" ]; then
        tar -czf "$BACKUP_DIR/${backup_name}.tar.gz" -C "$(dirname "$target")" "$(basename "$target")"
    else
        cp "$target" "$BACKUP_DIR/${backup_name}"
    fi
    echo -e "${GREEN}Backup created: $BACKUP_DIR/${backup_name}${NC}"
}

# Function to copy file or directory
copy_to_prod() {
    local source="$1"
    local target="$2"
    
    echo -e "${YELLOW}Copying $source to $target...${NC}"
    
    if [ -d "$source" ]; then
        cp -r "$source" "$target"
    else
        cp "$source" "$target"
    fi
    
    echo -e "${GREEN}Successfully copied $source to $target${NC}"
}

# Main migration logic
if [ $# -eq 0 ]; then
    echo -e "${YELLOW}No specific file/directory specified. Available options:${NC}"
    echo "1. Specific file: ./migrate_dev_to_prod.sh main.py"
    echo "2. Specific directory: ./migrate_dev_to_prod.sh routes/"
    echo "3. All Python files: ./migrate_dev_to_prod.sh *.py"
    echo "4. Templates: ./migrate_dev_to_prod.sh templates/"
    echo "5. Static files: ./migrate_dev_to_prod.sh static/"
    echo "6. Everything (BE CAREFUL): ./migrate_dev_to_prod.sh --all"
    exit 1
fi

if [ "$1" = "--all" ]; then
    echo -e "${RED}WARNING: This will copy ALL files from dev to production!${NC}"
    read -p "Are you sure? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        echo "Migration cancelled."
        exit 1
    fi
    
    # Create backup of entire production directory
    create_backup "$PROD_DIR"
    
    # Copy everything except venv, .git, and backups
    echo -e "${YELLOW}Copying all files from dev to production...${NC}"
    rsync -av --exclude='venv/' --exclude='.git/' --exclude='backups/' --exclude='*.log' "$DEV_DIR/" "$PROD_DIR/"
    
else
    # Handle specific file or directory
    for item in "$@"; do
        dev_path="$DEV_DIR/$item"
        prod_path="$PROD_DIR/$item"
        
        if [ ! -e "$dev_path" ]; then
            echo -e "${RED}Error: $dev_path does not exist${NC}"
            continue
        fi
        
        # Create backup of production file/directory
        if [ -e "$prod_path" ]; then
            create_backup "$prod_path"
        fi
        
        # Copy to production
        copy_to_prod "$dev_path" "$prod_path"
    done
fi

echo -e "${GREEN}Migration completed successfully!${NC}"
echo -e "${YELLOW}Don't forget to restart the production service:${NC}"
echo "sudo systemctl restart inertia-flask.service" 