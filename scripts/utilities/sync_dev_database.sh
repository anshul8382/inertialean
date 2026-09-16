#!/bin/bash

# Script to sync development database with production database
echo "Syncing development database with production database..."

# Database credentials
DB_USER="inertia_admin"
DB_PASS="!Nert!a2025$"
PROD_DB="inertia_app2025"
DEV_DB="inertia_app2025_dev"

# Missing tables that need to be copied
MISSING_TABLES=(
    "active_alerts_view"
    "agreement"
    "agreement_template"
    "agreement_variables"
    "alert"
    "alert_notification"
    "alert_report"
    "alert_summary_view"
    "ml_model_training_history"
    "ml_recommendation_feedback"
    "review"
    "review_schedule"
    "review_section"
    "review_share"
    "review_template"
    "review_workflow"
    "sla_configuration"
    "user_recommendation_preference"
)

echo "Copying missing tables from production to development..."

# Copy each missing table
for table in "${MISSING_TABLES[@]}"; do
    echo "Copying table: $table"
    
    # Get table structure
    mysqldump -u "$DB_USER" -p"$DB_PASS" --no-data "$PROD_DB" "$table" > "/tmp/${table}_structure.sql"
    
    # Import structure to dev database
    mysql -u "$DB_USER" -p"$DB_PASS" "$DEV_DB" < "/tmp/${table}_structure.sql"
    
    # Copy data (for tables that should have data)
    if [[ "$table" != *"view"* ]]; then
        echo "Copying data for table: $table"
        mysqldump -u "$DB_USER" -p"$DB_PASS" --no-create-info "$PROD_DB" "$table" > "/tmp/${table}_data.sql"
        mysql -u "$DB_USER" -p"$DB_PASS" "$DEV_DB" < "/tmp/${table}_data.sql"
    fi
    
    # Clean up temporary files
    rm -f "/tmp/${table}_structure.sql" "/tmp/${table}_data.sql"
done

echo "Database sync completed!"
echo "Verifying table count..."

# Verify the sync
PROD_COUNT=$(mysql -u "$DB_USER" -p"$DB_PASS" -e "USE $PROD_DB; SHOW TABLES;" | wc -l)
DEV_COUNT=$(mysql -u "$DB_USER" -p"$DB_PASS" -e "USE $DEV_DB; SHOW TABLES;" | wc -l)

echo "Production database tables: $PROD_COUNT"
echo "Development database tables: $DEV_COUNT"

if [ "$PROD_COUNT" -eq "$DEV_COUNT" ]; then
    echo "✅ Database sync successful! Both databases now have the same number of tables."
else
    echo "❌ Database sync may have issues. Table counts don't match."
fi
