#!/bin/bash
# Deployment script for dev/staging/prod

set -e

ENVIRONMENT=$1
BRANCH=$2

if [ -z "$ENVIRONMENT" ] || [ -z "$BRANCH" ]; then
    echo "Usage: ./deploy.sh [dev|staging|prod] [branch-name]"
    exit 1
fi

APP_DIR="/home/inertia/app"
cd "$APP_DIR"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}Deploying $BRANCH to $ENVIRONMENT...${NC}"

# 1. Backup database (only for staging/prod)
if [ "$ENVIRONMENT" != "dev" ]; then
    echo -e "${YELLOW}Backing up database...${NC}"
    ./scripts/deployment/backup_db.sh $ENVIRONMENT
fi

# 2. Pull latest code
echo -e "${YELLOW}Pulling latest code from $BRANCH...${NC}"
git fetch origin
git checkout $BRANCH
git pull origin $BRANCH

# 3. Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# 4. Install dependencies
echo -e "${YELLOW}Installing dependencies...${NC}"
pip install -r requirements.txt

# 5. Run migrations
echo -e "${YELLOW}Running database migrations...${NC}"
./scripts/deployment/migrate.sh $ENVIRONMENT upgrade

# 6. Restart application
echo -e "${YELLOW}Restarting application...${NC}"
if [ "$ENVIRONMENT" == "prod" ]; then
    sudo systemctl restart inertia-app 2>/dev/null || sudo systemctl restart inertia-app-runpy 2>/dev/null || echo "Note: Service restart may require manual intervention"
elif [ "$ENVIRONMENT" == "staging" ]; then
    # For staging, you might use a different service name
    sudo systemctl restart inertia-app-staging 2>/dev/null || pkill -f "flask run" || true
    export FLASK_ENV=staging
    if [ -f ".env.staging" ]; then
        source .env.staging
    fi
    nohup python3 run.py > logs/staging.log 2>&1 &
else
    # Development
    pkill -f "python3 run.py" || true
    export FLASK_ENV=development
    if [ -f ".env.development" ]; then
        source .env.development
    fi
    nohup python3 run.py > logs/dev.log 2>&1 &
fi

echo -e "${GREEN}Deployment complete!${NC}"


