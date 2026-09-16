#!/bin/bash

# Quick Migration Helper Script
# Combines common migration operations

set -e

# Configuration
DEV_DIR="/home/inertia/app_dev"
PROD_DIR="/home/inertia/app"
BACKUP_DIR="/home/inertia/backups"
DATE=$(date +%Y%m%d_%H%M%S)

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Quick Migration Helper ===${NC}"

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTION] [files/directories...]"
    echo ""
    echo "Options:"
    echo "  --backup-only     Create backup only"
    echo "  --code-only       Migrate code only (no database)"
    echo "  --db-only         Migrate database only (no code)"
    echo "  --full            Full migration (backup + code + database)"
    echo "  --verify          Verify migration success"
    echo ""
    echo "Examples:"
    echo "  $0 --backup-only                    # Just create backup"
    echo "  $0 --code-only routes/ templates/   # Migrate specific directories"
    echo "  $0 --full                           # Complete migration"
    echo "  $0 --verify                         # Check if migration worked"
}

# Function to create backup
create_backup() {
    echo -e "${YELLOW}Creating backup...${NC}"
    ./migrate_database.sh --full-backup
    echo -e "${GREEN}Backup completed!${NC}"
}

# Function to migrate code
migrate_code() {
    local items="$@"
    echo -e "${YELLOW}Migrating code...${NC}"
    
    if [ -z "$items" ]; then
        echo -e "${YELLOW}No specific files provided. Migrating common directories...${NC}"
        ./migrate_dev_to_prod.sh routes/
        ./migrate_dev_to_prod.sh templates/
        ./migrate_dev_to_prod.sh static/
    else
        for item in $items; do
            ./migrate_dev_to_prod.sh "$item"
        done
    fi
    echo -e "${GREEN}Code migration completed!${NC}"
}

# Function to migrate database
migrate_database() {
    echo -e "${YELLOW}Migrating database schema...${NC}"
    ./migrate_database.sh --schema-only
    echo -e "${GREEN}Database migration completed!${NC}"
}

# Function to restart application
restart_app() {
    echo -e "${YELLOW}Restarting application...${NC}"
    ./manage_app_optimized.sh restart production
    echo -e "${GREEN}Application restarted!${NC}"
}

# Function to verify migration
verify_migration() {
    echo -e "${YELLOW}Verifying migration...${NC}"
    
    # Check if app is running
    if ./manage_app_optimized.sh health > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Application is healthy${NC}"
    else
        echo -e "${RED}✗ Application health check failed${NC}"
        return 1
    fi
    
    # Check status
    echo -e "${YELLOW}Application status:${NC}"
    ./manage_app_optimized.sh status
    
    echo -e "${GREEN}Migration verification completed!${NC}"
}

# Main logic
case "${1:-}" in
    --backup-only)
        create_backup
        ;;
        
    --code-only)
        shift
        migrate_code "$@"
        restart_app
        ;;
        
    --db-only)
        create_backup
        migrate_database
        restart_app
        ;;
        
    --full)
        create_backup
        migrate_code
        migrate_database
        restart_app
        ;;
        
    --verify)
        verify_migration
        ;;
        
    *)
        show_usage
        exit 1
        ;;
esac

echo -e "${GREEN}Migration process completed!${NC}"
echo -e "${YELLOW}Don't forget to verify the migration with: $0 --verify${NC}"
