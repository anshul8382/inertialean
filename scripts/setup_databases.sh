#!/bin/bash
# Setup databases for dev/staging/prod

set -e

DB_USER="inertia_admin"
DB_PASSWORD="!Nert!a2025$"

echo "Setting up databases..."
echo "This will create:"
echo "  - inertia_app2025_dev (Development)"
echo "  - inertia_app2025_staging (Staging)"
echo "  - inertia_app2025 (Production - if not exists)"
echo ""
read -p "Enter MySQL root password: " -s ROOT_PASSWORD
echo ""

mysql -u root -p"$ROOT_PASSWORD" <<EOF
CREATE DATABASE IF NOT EXISTS inertia_app2025_dev CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS inertia_app2025_staging CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS inertia_app2025 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

GRANT ALL PRIVILEGES ON inertia_app2025_dev.* TO '$DB_USER'@'localhost';
GRANT ALL PRIVILEGES ON inertia_app2025_staging.* TO '$DB_USER'@'localhost';
GRANT ALL PRIVILEGES ON inertia_app2025.* TO '$DB_USER'@'localhost';

FLUSH PRIVILEGES;
EOF

echo ""
echo "Databases created successfully!"
echo ""
echo "Next steps:"
echo "  1. Copy .env.*.template files to .env.* and update values"
echo "  2. Initialize dev database: FLASK_ENV=development flask db upgrade"


