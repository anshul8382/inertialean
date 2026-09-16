#!/bin/bash
# Database backup script

set -e

ENVIRONMENT=$1
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
APP_DIR="/home/inertia/app"
BACKUP_DIR="${APP_DIR}/backups"

mkdir -p "$BACKUP_DIR"

cd "$APP_DIR"

# Load environment
if [ -f ".env.$ENVIRONMENT" ]; then
    set -a
    source .env.$ENVIRONMENT
    set +a
else
    echo "Error: .env.$ENVIRONMENT not found"
    exit 1
fi

# Backup database
BACKUP_FILE="${BACKUP_DIR}/${DB_NAME}_${TIMESTAMP}.sql"
echo "Backing up database: $DB_NAME"
echo "Backup file: $BACKUP_FILE"

mysqldump -u "$DB_USER" -p"$DB_PASSWORD" -h "$DB_HOST" \
    "$DB_NAME" > "$BACKUP_FILE"

# Compress backup
gzip "$BACKUP_FILE"
echo "Backup created: ${BACKUP_FILE}.gz"

# Keep only last 30 days of backups
find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -mtime +30 -delete

echo "Backup completed successfully!"


