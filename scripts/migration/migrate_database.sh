#!/bin/bash

# Database Migration Script (Dev to Production)
# Usage: ./migrate_database.sh [--schema-only] [--data-only] [--full]

set -e

# Configuration
DEV_DB="inertia_app2025_dev"
PROD_DB="inertia_app2025"
DB_USER="inertia_admin"
DB_PASS="!Nert!a2025$"
BACKUP_DIR="/home/inertia/backups"
DATE=$(date +%Y%m%d_%H%M%S)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== Database Migration Script ===${NC}"

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Function to create database backup
backup_database() {
    local db_name="$1"
    local backup_file="$BACKUP_DIR/backup_${db_name}_${DATE}.sql"
    
    echo -e "${YELLOW}Creating backup of $db_name...${NC}"
    mysqldump -u "$DB_USER" -p"$DB_PASS" "$db_name" > "$backup_file"
    echo -e "${GREEN}Backup created: $backup_file${NC}"
}

# Function to create full system backup
create_full_backup() {
    echo -e "${YELLOW}Creating full system backup...${NC}"
    
    # Backup production code
    local code_backup="$BACKUP_DIR/backup_production_code_${DATE}.tar.gz"
    tar -czf "$code_backup" -C /home/inertia app/
    echo -e "${GREEN}Code backup created: $code_backup${NC}"
    
    # Backup production database
    backup_database "$PROD_DB"
    
    # Create backup manifest
    local manifest="$BACKUP_DIR/backup_manifest_${DATE}.txt"
    echo "Full System Backup - $(date)" > "$manifest"
    echo "Code backup: $code_backup" >> "$manifest"
    echo "Database backup: backup_${PROD_DB}_${DATE}.sql" >> "$manifest"
    echo "Backup size:" >> "$manifest"
    du -h "$code_backup" >> "$manifest"
    du -h "$BACKUP_DIR/backup_${PROD_DB}_${DATE}.sql" >> "$manifest"
    
    echo -e "${GREEN}Full backup manifest: $manifest${NC}"
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTION]"
    echo ""
    echo "Options:"
    echo "  --schema-only    Migrate only schema changes (structure)"
    echo "  --data-only      Migrate only data (content)"
    echo "  --full           Full migration (schema + data)"
    echo "  --backup-only    Create backup only"
    echo "  --full-backup    Create complete system backup (code + database)"
    echo ""
    echo "Examples:"
    echo "  $0 --schema-only    # Safe: only table structures"
    echo "  $0 --data-only      # Safe: only data, no structure changes"
    echo "  $0 --full           # DANGEROUS: full sync (will overwrite production data)"
    echo "  $0 --backup-only    # Just create database backup"
    echo "  $0 --full-backup    # Complete system backup"
}

# Main logic
case "${1:-}" in
    --schema-only)
        echo -e "${YELLOW}Migrating schema only...${NC}"
        backup_database "$PROD_DB"
        
        # Get schema from dev and apply to prod
        echo -e "${YELLOW}Extracting schema from dev database...${NC}"
        mysqldump -u "$DB_USER" -p"$DB_PASS" --no-data "$DEV_DB" > /tmp/dev_schema.sql
        
        echo -e "${YELLOW}Applying schema to production database...${NC}"
        mysql -u "$DB_USER" -p"$DB_PASS" "$PROD_DB" < /tmp/dev_schema.sql
        
        rm -f /tmp/dev_schema.sql
        echo -e "${GREEN}Schema migration completed!${NC}"
        ;;
        
    --data-only)
        echo -e "${YELLOW}Migrating data only...${NC}"
        backup_database "$PROD_DB"
        
        # Get data from dev and apply to prod (excluding certain tables)
        echo -e "${YELLOW}Extracting data from dev database...${NC}"
        mysqldump -u "$DB_USER" -p"$DB_PASS" --no-create-info --ignore-table="$DEV_DB.user" "$DEV_DB" > /tmp/dev_data.sql
        
        echo -e "${YELLOW}Applying data to production database...${NC}"
        mysql -u "$DB_USER" -p"$DB_PASS" "$PROD_DB" < /tmp/dev_data.sql
        
        rm -f /tmp/dev_data.sql
        echo -e "${GREEN}Data migration completed!${NC}"
        ;;
        
    --full)
        echo -e "${RED}WARNING: This will completely overwrite the production database!${NC}"
        read -p "Are you absolutely sure? Type 'YES' to confirm: " confirm
        if [ "$confirm" != "YES" ]; then
            echo "Migration cancelled."
            exit 1
        fi
        
        echo -e "${YELLOW}Performing full database migration...${NC}"
        backup_database "$PROD_DB"
        
        # Drop and recreate production database
        echo -e "${YELLOW}Dropping production database...${NC}"
        mysql -u "$DB_USER" -p"$DB_PASS" -e "DROP DATABASE IF EXISTS $PROD_DB;"
        mysql -u "$DB_USER" -p"$DB_PASS" -e "CREATE DATABASE $PROD_DB;"
        
        # Copy everything from dev to prod
        echo -e "${YELLOW}Copying dev database to production...${NC}"
        mysqldump -u "$DB_USER" -p"$DB_PASS" "$DEV_DB" | mysql -u "$DB_USER" -p"$DB_PASS" "$PROD_DB"
        
        echo -e "${GREEN}Full database migration completed!${NC}"
        ;;
        
    --backup-only)
        echo -e "${YELLOW}Creating database backups only...${NC}"
        backup_database "$DEV_DB"
        backup_database "$PROD_DB"
        echo -e "${GREEN}Database backups completed!${NC}"
        ;;
        
    --full-backup)
        echo -e "${YELLOW}Creating complete system backup...${NC}"
        create_full_backup
        echo -e "${GREEN}Complete system backup finished!${NC}"
        ;;
        
    *)
        show_usage
        exit 1
        ;;
esac

echo -e "${YELLOW}Don't forget to restart the production service:${NC}"
echo "sudo systemctl restart inertia-flask.service" 