#!/bin/bash
#
# Setup Test and Production Branch Workflow
# This script sets up dedicated test and prod branches for environment management
#

set -e

# Configuration
TEST_DIR="/home/inertia/app_test"
PROD_DIR="/home/inertia/app"
TEST_BRANCH="test"
PROD_BRANCH="prod"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Functions
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

print_header "Setting Up Test and Production Branch Workflow"

# Step 1: Setup Test Branch
print_header "Step 1: Setting Up Test Branch"
cd "$TEST_DIR"

if [ ! -d ".git" ]; then
    print_error "Test directory is not a git repository"
    exit 1
fi

# Fetch latest from remote
git fetch origin 2>/dev/null || print_warning "Could not fetch from remote"

# Check if test branch exists
if git show-ref --verify --quiet refs/heads/$TEST_BRANCH; then
    git checkout $TEST_BRANCH
    print_success "Checked out existing $TEST_BRANCH branch"
else
    # Create test branch from current branch or main
    if git show-ref --verify --quiet refs/remotes/origin/$TEST_BRANCH; then
        git checkout -b $TEST_BRANCH origin/$TEST_BRANCH
        print_success "Created local $TEST_BRANCH branch from remote"
    else
        CURRENT_BRANCH=$(git branch --show-current)
        git checkout -b $TEST_BRANCH
        print_success "Created new $TEST_BRANCH branch from $CURRENT_BRANCH"
    fi
fi

# Push test branch to remote if it doesn't exist
if ! git show-ref --verify --quiet refs/remotes/origin/$TEST_BRANCH; then
    git push -u origin $TEST_BRANCH
    print_success "Pushed $TEST_BRANCH branch to remote"
else
    print_success "$TEST_BRANCH branch exists on remote"
fi

# Step 2: Setup Production Branch
print_header "Step 2: Setting Up Production Branch"
cd "$PROD_DIR"

if [ ! -d ".git" ]; then
    print_error "Production directory is not a git repository"
    exit 1
fi

# Fetch latest from remote
git fetch origin 2>/dev/null || print_warning "Could not fetch from remote"

# Check if prod branch exists
if git show-ref --verify --quiet refs/heads/$PROD_BRANCH; then
    print_success "Local $PROD_BRANCH branch already exists"
else
    # Create prod branch from main or current branch
    if git show-ref --verify --quiet refs/remotes/origin/main; then
        git checkout -b $PROD_BRANCH origin/main
        print_success "Created $PROD_BRANCH branch from main"
    elif git show-ref --verify --quiet refs/heads/main; then
        git checkout main
        git checkout -b $PROD_BRANCH
        print_success "Created $PROD_BRANCH branch from local main"
    else
        CURRENT_BRANCH=$(git branch --show-current)
        git checkout -b $PROD_BRANCH
        print_success "Created $PROD_BRANCH branch from $CURRENT_BRANCH"
    fi
fi

# Switch to prod branch
git checkout $PROD_BRANCH

# Push prod branch to remote if it doesn't exist
if ! git show-ref --verify --quiet refs/remotes/origin/$PROD_BRANCH; then
    git push -u origin $PROD_BRANCH
    print_success "Pushed $PROD_BRANCH branch to remote"
else
    print_success "$PROD_BRANCH branch exists on remote"
fi

# Step 3: Verify Setup
print_header "Step 3: Verifying Setup"

cd "$TEST_DIR"
TEST_CURRENT=$(git branch --show-current)
print_success "Test environment is on branch: $TEST_CURRENT"

cd "$PROD_DIR"
PROD_CURRENT=$(git branch --show-current)
print_success "Production environment is on branch: $PROD_CURRENT"

# Summary
print_header "Setup Summary"
print_success "Branch workflow setup complete!"
echo ""
echo "Branch Structure:"
echo "  Test Environment:     $TEST_DIR → branch: $TEST_BRANCH"
echo "  Production Environment: $PROD_DIR → branch: $PROD_BRANCH"
echo ""
echo "Workflow:"
echo "  1. Make changes in test environment (on $TEST_BRANCH branch)"
echo "  2. Test thoroughly in test environment"
echo "  3. Use migrate_test_to_prod.sh to move changes to production"
echo "  4. Production changes are committed to $PROD_BRANCH branch"
echo ""
print_warning "Next steps:"
echo "  - Ensure test environment is on $TEST_BRANCH branch"
echo "  - Ensure production environment is on $PROD_BRANCH branch"
echo "  - Use ./scripts/migration/migrate_test_to_prod.sh to migrate changes"
