#!/bin/bash
#
# Setup Test Codebase Directory
# Creates /home/inertia/app_test with separate git setup
#

set -e

PROD_DIR="/home/inertia/app"
TEST_DIR="/home/inertia/app_test"
BACKUP_DIR="/home/inertia/backups"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_header "Setting Up Test Codebase Directory"

# Check if test directory already exists
if [ -d "$TEST_DIR" ]; then
    print_warning "Test directory already exists: $TEST_DIR"
    read -p "Do you want to backup and recreate it? (yes/no): " confirm
    if [ "$confirm" = "yes" ]; then
        BACKUP_NAME="app_test_backup_$(date +%Y%m%d_%H%M%S)"
        mv "$TEST_DIR" "/home/inertia/$BACKUP_NAME"
        print_success "Backup created: /home/inertia/$BACKUP_NAME"
    else
        print_error "Setup cancelled"
        exit 1
    fi
fi

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Copy production to test directory
print_header "Copying Production Codebase to Test Directory"
print_warning "This may take a few minutes..."

rsync -av \
    --exclude='venv/' \
    --exclude='.git/' \
    --exclude='backups/' \
    --exclude='*.log' \
    --exclude='*.pid' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='instance/' \
    --exclude='.env' \
    "$PROD_DIR/" "$TEST_DIR/"

print_success "Code copied successfully"

# Navigate to test directory
cd "$TEST_DIR"

# Update configuration for test environment
print_header "Updating Configuration for Test Environment"

# Update .env.test if it exists
if [ -f ".env.test" ]; then
    sed -i "s|DB_NAME=inertia_app2025|DB_NAME=inertia_app2025_test|g" .env.test
    sed -i "s|FLASK_ENV=production|FLASK_ENV=test|g" .env.test
    print_success "Updated .env.test"
fi

# Initialize Git repository for test
print_header "Setting Up Git Repository"

# Check if production has git remote
cd "$PROD_DIR"
if [ -d ".git" ]; then
    REMOTE_URL=$(git remote get-url origin 2>/dev/null || echo "")
    CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "main")
    
    print_success "Production has git repository"
    print_warning "Remote: $REMOTE_URL"
    print_warning "Current branch: $CURRENT_BRANCH"
    
    # Initialize git in test directory
    cd "$TEST_DIR"
    
    # Initialize git if not exists
    if [ ! -d ".git" ]; then
        git init
        print_success "Git repository initialized"
    fi
    
    # Add remote if it exists
    if [ -n "$REMOTE_URL" ]; then
        git remote remove origin 2>/dev/null || true
        git remote add origin "$REMOTE_URL"
        print_success "Git remote added: $REMOTE_URL"
        
        # Fetch remote branches
        git fetch origin 2>/dev/null || print_warning "Could not fetch from remote"
        
        # Create or checkout test branch
        if git show-ref --verify --quiet refs/remotes/origin/test; then
            git checkout -b test origin/test 2>/dev/null || git checkout test
            print_success "Checked out test branch"
        else
            git checkout -b test 2>/dev/null || true
            print_success "Created test branch"
        fi
    else
        # No remote, create local test branch
        git checkout -b test 2>/dev/null || git checkout test
        print_success "Created local test branch"
    fi
    
    # Initial commit if needed
    if [ -z "$(git status --porcelain)" ]; then
        print_warning "No changes to commit"
    else
        git add .
        git commit -m "Initial test environment setup - $(date '+%Y-%m-%d %H:%M:%S')" || true
        print_success "Initial commit created"
    fi
else
    print_warning "Production does not have git repository"
    print_warning "Initializing new git repository in test directory"
    cd "$TEST_DIR"
    git init
    git checkout -b test
    git add .
    git commit -m "Initial test environment setup - $(date '+%Y-%m-%d %H:%M:%S')"
    print_success "New git repository initialized"
fi

# Create virtual environment for test
print_header "Setting Up Virtual Environment"
if [ ! -d "venv" ]; then
    print_warning "Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    print_success "Virtual environment created"
else
    print_warning "Virtual environment already exists, skipping..."
fi

# Make scripts executable
print_header "Setting Up Scripts"
chmod +x start_test.sh 2>/dev/null || true
chmod +x scripts/utilities/*.sh 2>/dev/null || true
chmod +x scripts/migration/*.sh 2>/dev/null || true

print_header "Setup Complete!"
print_success "Test codebase directory created: $TEST_DIR"
print_success "Test database: inertia_app2025_test"
print_success "Test port: 5002"
echo ""
print_warning "Next steps:"
echo "  1. cd $TEST_DIR"
echo "  2. ./scripts/utilities/setup_test_database.sh (if not already done)"
echo "  3. ./start_test.sh"
echo ""
print_warning "Git setup:"
echo "  - Current branch: $(cd $TEST_DIR && git branch --show-current 2>/dev/null || echo 'test')"
echo "  - Remote: $(cd $TEST_DIR && git remote get-url origin 2>/dev/null || echo 'Not configured')"
echo ""
print_success "✅ Test codebase setup completed!"

