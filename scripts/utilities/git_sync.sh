#!/bin/bash
#
# Git Sync Utility
# Syncs git repositories between test and production environments
#

set -e

PROD_DIR="/home/inertia/app"
TEST_DIR="/home/inertia/app_test"

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

# Function to sync from remote
sync_from_remote() {
    local dir=$1
    local branch=$2
    
    if [ ! -d "$dir/.git" ]; then
        print_warning "$dir is not a git repository"
        return 1
    fi
    
    cd "$dir"
    
    # Fetch latest
    print_warning "Fetching from remote..."
    git fetch origin 2>/dev/null || print_warning "Failed to fetch from remote"
    
    # Check if branch exists on remote
    if git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
        print_warning "Pulling latest changes for branch: $branch"
        git pull origin "$branch" || print_warning "Pull failed"
        print_success "Synced $dir from remote"
    else
        print_warning "Branch $branch does not exist on remote"
    fi
}

# Function to push to remote
push_to_remote() {
    local dir=$1
    local branch=$2
    
    if [ ! -d "$dir/.git" ]; then
        print_warning "$dir is not a git repository"
        return 1
    fi
    
    cd "$dir"
    
    # Check if there are commits to push
    if git rev-list HEAD ^origin/$branch 2>/dev/null | grep -q .; then
        print_warning "Pushing $branch to remote..."
        git push origin "$branch" || print_warning "Push failed"
        print_success "Pushed $dir to remote"
    else
        print_warning "No commits to push in $dir"
    fi
}

print_header "Git Sync Utility"

# Sync test environment
if [ -d "$TEST_DIR/.git" ]; then
    print_header "Syncing Test Environment"
    TEST_BRANCH=$(cd "$TEST_DIR" && git branch --show-current 2>/dev/null || echo "test")
    sync_from_remote "$TEST_DIR" "$TEST_BRANCH"
fi

# Sync production environment
if [ -d "$PROD_DIR/.git" ]; then
    print_header "Syncing Production Environment"
    PROD_BRANCH=$(cd "$PROD_DIR" && git branch --show-current 2>/dev/null || echo "main")
    sync_from_remote "$PROD_DIR" "$PROD_BRANCH"
fi

print_header "Sync Complete"

