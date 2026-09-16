#!/bin/bash

# Script to set up test database for Inertia Investment App
# This script creates the test database and optionally copies schema from production

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} ✅ $1"
}

print_warning() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} ⚠️  $1"
}

print_error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} ❌ $1"
}

# Database credentials
DB_USER="inertia_admin"
DB_PASS="!Nert!a2025$"
TEST_DB="inertia_app2025_test"
PROD_DB="inertia_app2025"

print_status "Setting up test database: $TEST_DB"

# Check if database already exists
if mysql -u "$DB_USER" -p"$DB_PASS" -e "USE $TEST_DB" 2>/dev/null; then
    print_warning "Test database $TEST_DB already exists"
    read -p "Do you want to drop and recreate it? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        print_status "Dropping existing test database..."
        mysql -u "$DB_USER" -p"$DB_PASS" -e "DROP DATABASE IF EXISTS $TEST_DB;"
    else
        print_status "Keeping existing database. Exiting."
        exit 0
    fi
fi

# Create test database
print_status "Creating test database..."
mysql -u "$DB_USER" -p"$DB_PASS" -e "CREATE DATABASE IF NOT EXISTS $TEST_DB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
print_success "Test database created"

# Option to copy schema from production
print_status "Do you want to copy schema from production database? (Recommended: Yes)"
read -p "Copy schema from $PROD_DB? (Y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    if mysql -u "$DB_USER" -p"$DB_PASS" -e "USE $PROD_DB" 2>/dev/null; then
        print_status "Copying schema from production database..."
        mysqldump -u "$DB_USER" -p"$DB_PASS" --no-data --skip-triggers --routines --events "$PROD_DB" | mysql -u "$DB_USER" -p"$DB_PASS" "$TEST_DB"
        print_success "Schema copied successfully"
        
        # Option to copy minimal test data
        print_status "Do you want to copy minimal test data? (Recommended: No, use empty database)"
        read -p "Copy test data? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            print_status "Copying test data (this may take a while)..."
            # Copy data but exclude large or sensitive tables
            mysqldump -u "$DB_USER" -p"$DB_PASS" --no-create-info --skip-triggers \
                --ignore-table="$PROD_DB.transactions" \
                --ignore-table="$PROD_DB.holdings" \
                --ignore-table="$PROD_DB.cashflows" \
                --ignore-table="$PROD_DB.logs" \
                "$PROD_DB" | mysql -u "$DB_USER" -p"$DB_PASS" "$TEST_DB" 2>/dev/null || true
            print_success "Test data copied"
        fi
    else
        print_error "Production database $PROD_DB not accessible"
        exit 1
    fi
else
    print_status "Skipping schema copy. You'll need to run migrations manually."
fi

# Verify database setup
print_status "Verifying test database setup..."
TABLE_COUNT=$(mysql -u "$DB_USER" -p"$DB_PASS" -e "USE $TEST_DB; SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '$TEST_DB';" 2>/dev/null | tail -1)

if [ "$TABLE_COUNT" -gt 0 ]; then
    print_success "Test database setup complete! Found $TABLE_COUNT tables."
else
    print_warning "Test database is empty. Run migrations to create tables:"
    echo "  FLASK_ENV=test flask db upgrade"
fi

echo ""
print_success "Test database setup completed!"
print_status "Next steps:"
echo "  1. Run migrations: FLASK_ENV=test flask db upgrade"
echo "  2. Start test server: ./start_test.sh"
echo "  3. Run tests: FLASK_ENV=test python -m pytest tests/"

