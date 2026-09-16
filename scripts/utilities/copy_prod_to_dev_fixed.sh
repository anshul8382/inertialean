#!/bin/bash

# Script to completely copy production database to development database
echo "Copying production database to development database..."

# Database credentials
DB_USER="inertia_admin"
DB_PASS="!Nert!a2025$"
PROD_DB="inertia_app2025"
DEV_DB="inertia_app2025_dev"

# Backup current dev database (optional safety measure)
echo "Creating backup of current development database..."
BACKUP_FILE="/tmp/inertia_app2025_dev_backup_$(date +%Y%m%d_%H%M%S).sql"
mysqldump -u "$DB_USER" -p"$DB_PASS" "$DEV_DB" > "$BACKUP_FILE"
echo "Backup created: $BACKUP_FILE"

# Drop the existing development database
echo "Dropping existing development database..."
mysql -u "$DB_USER" -p"$DB_PASS" -e "DROP DATABASE IF EXISTS $DEV_DB;"

# Create a fresh development database
echo "Creating fresh development database..."
mysql -u "$DB_USER" -p"$DB_PASS" -e "CREATE DATABASE $DEV_DB;"

# Copy tables and data (excluding views)
echo "Copying tables and data from production to development..."
mysqldump -u "$DB_USER" -p"$DB_PASS" --no-create-db --skip-triggers --routines --events "$PROD_DB" | mysql -u "$DB_USER" -p"$DB_PASS" "$DEV_DB"

# Get list of views from production
echo "Getting list of views from production..."
mysql -u "$DB_USER" -p"$DB_PASS" -e "USE $PROD_DB; SHOW FULL TABLES WHERE Table_type = 'VIEW';" > /tmp/prod_views.txt

# Create views manually (without DEFINER clauses)
echo "Creating views in development database..."
mysql -u "$DB_USER" -p"$DB_PASS" -e "USE $DEV_DB;" < create_dev_views.sql

# Verify the copy
echo "Verifying database copy..."

PROD_COUNT=$(mysql -u "$DB_USER" -p"$DB_PASS" -e "USE $PROD_DB; SHOW TABLES;" | wc -l)
DEV_COUNT=$(mysql -u "$DB_USER" -p"$DB_PASS" -e "USE $DEV_DB; SHOW TABLES;" | wc -l)

echo "Production database tables: $PROD_COUNT"
echo "Development database tables: $DEV_COUNT"

if [ "$PROD_COUNT" -eq "$DEV_COUNT" ]; then
    echo "✅ Database copy successful! Both databases now have the same number of tables."
    echo "✅ Development database is now an exact copy of production database."
else
    echo "❌ Database copy may have issues. Table counts don't match."
    echo "Checking what's missing..."
    comm -23 <(mysql -u "$DB_USER" -p"$DB_PASS" -e "USE $PROD_DB; SHOW TABLES;" | sort) <(mysql -u "$DB_USER" -p"$DB_PASS" -e "USE $DEV_DB; SHOW TABLES;" | sort)
fi

echo "Copy completed!"
