#!/bin/bash
# Migration script for dev/staging/prod

set -e

ENVIRONMENT=$1
MIGRATION_TYPE=$2  # 'upgrade' or 'downgrade'
VERSION=$3  # Optional: specific version

if [ -z "$ENVIRONMENT" ] || [ -z "$MIGRATION_TYPE" ]; then
    echo "Usage: ./migrate.sh [dev|staging|prod] [upgrade|downgrade] [version]"
    exit 1
fi

APP_DIR="/home/inertia/app"
cd "$APP_DIR"

# Load environment
export FLASK_ENV=$ENVIRONMENT
if [ -f ".env.$ENVIRONMENT" ]; then
    set -a
    source .env.$ENVIRONMENT
    set +a
else
    echo "Warning: .env.$ENVIRONMENT not found"
fi

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Run migration
if [ "$MIGRATION_TYPE" == "upgrade" ]; then
    if [ -z "$VERSION" ]; then
        echo "Running: flask db upgrade"
        flask db upgrade
    else
        echo "Running: flask db upgrade $VERSION"
        flask db upgrade $VERSION
    fi
elif [ "$MIGRATION_TYPE" == "downgrade" ]; then
    if [ -z "$VERSION" ]; then
        echo "Running: flask db downgrade -1"
        flask db downgrade -1
    else
        echo "Running: flask db downgrade $VERSION"
        flask db downgrade $VERSION
    fi
else
    echo "Invalid migration type. Use 'upgrade' or 'downgrade'"
    exit 1
fi

echo "Migration completed successfully!"


