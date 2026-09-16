#!/bin/bash
#
# Test to Production Migration Script with Git Integration
# Usage: ./migrate_test_to_prod.sh [file_or_directory] [--commit-message "message"]
#

set -e

# Configuration
TEST_DIR="/home/inertia/app_test"
PROD_DIR="/home/inertia/app"
BACKUP_DIR="/home/inertia/backups"
TEST_BRANCH="test"
PROD_BRANCH="prod"
DATE=$(date +%Y%m%d_%H%M%S)

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

# Check directories
if [ ! -d "$TEST_DIR" ]; then
    print_error "Test directory not found: $TEST_DIR"
    exit 1
fi

if [ ! -d "$PROD_DIR" ]; then
    print_error "Production directory not found: $PROD_DIR"
    exit 1
fi

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Parse arguments
COMMIT_MSG=""
FILES_TO_MIGRATE=()
PUSH_TO_REMOTE=false
CREATE_TAG=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --commit-message|--msg)
            COMMIT_MSG="$2"
            shift 2
            ;;
        --push)
            PUSH_TO_REMOTE=true
            shift
            ;;
        --tag)
            CREATE_TAG=true
            shift
            ;;
        --all)
            FILES_TO_MIGRATE=("--all")
            shift
            ;;
        *)
            FILES_TO_MIGRATE+=("$1")
            shift
            ;;
    esac
done

if [ ${#FILES_TO_MIGRATE[@]} -eq 0 ]; then
    print_error "No files specified for migration"
    echo ""
    echo "Usage:"
    echo "  ./migrate_test_to_prod.sh <file_or_directory>"
    echo "  ./migrate_test_to_prod.sh routes/ templates/"
    echo "  ./migrate_test_to_prod.sh --all"
    echo ""
    echo "Options:"
    echo "  --commit-message 'message'  - Commit message for git"
    echo "  --push                      - Push to remote after migration"
    echo "  --tag                       - Create git tag after migration"
    exit 1
fi

print_header "Test to Production Migration with Git Integration"

# Step 1: Check Git status in test
print_header "Step 1: Checking Git Status (Test Environment)"
cd "$TEST_DIR"

if [ ! -d ".git" ]; then
    print_warning "Test directory is not a git repository"
else
    # Ensure we're on test branch
    CURRENT_BRANCH=$(git branch --show-current)
    if [ "$CURRENT_BRANCH" != "$TEST_BRANCH" ]; then
        print_warning "Not on $TEST_BRANCH branch (currently on $CURRENT_BRANCH)"
        if git show-ref --verify --quiet refs/heads/$TEST_BRANCH; then
            git checkout $TEST_BRANCH
            print_success "Switched to $TEST_BRANCH branch"
        else
            print_warning "Creating $TEST_BRANCH branch from current branch"
            git checkout -b $TEST_BRANCH
        fi
    else
        print_success "On $TEST_BRANCH branch"
    fi
    
    # Check for uncommitted changes
    if [ -n "$(git status --porcelain)" ]; then
        print_warning "You have uncommitted changes in test directory"
        read -p "Commit changes before migration? (yes/no): " commit_before
        if [ "$commit_before" = "yes" ]; then
            git add .
            git commit -m "Pre-migration commit: $(date '+%Y-%m-%d %H:%M:%S')"
            print_success "Changes committed"
        fi
    fi
    
    CURRENT_BRANCH=$(git branch --show-current)
    print_success "Current branch: $CURRENT_BRANCH"
    
    # Show what will be migrated
    if [ "${FILES_TO_MIGRATE[0]}" != "--all" ]; then
        print_warning "Files to migrate:"
        for file in "${FILES_TO_MIGRATE[@]}"; do
            echo "  - $file"
        done
    fi
fi

# Step 2: Create backup
print_header "Step 2: Creating Production Backup"
if [ "${FILES_TO_MIGRATE[0]}" = "--all" ]; then
    BACKUP_NAME="backup_prod_all_${DATE}.tar.gz"
    print_warning "Creating full production backup..."
    tar -czf "$BACKUP_DIR/$BACKUP_NAME" -C "$PROD_DIR" . 2>/dev/null || true
    print_success "Backup created: $BACKUP_NAME"
else
    for item in "${FILES_TO_MIGRATE[@]}"; do
        prod_path="$PROD_DIR/$item"
        if [ -e "$prod_path" ]; then
            BACKUP_NAME="backup_${DATE}_$(echo "$item" | tr '/' '_').tar.gz"
            if [ -d "$prod_path" ]; then
                tar -czf "$BACKUP_DIR/$BACKUP_NAME" -C "$PROD_DIR" "$item" 2>/dev/null || true
            else
                cp "$prod_path" "$BACKUP_DIR/backup_${DATE}_$(basename "$item")" 2>/dev/null || true
            fi
            print_success "Backup created for: $item"
        fi
    done
fi

# Step 3: Migrate files
print_header "Step 3: Migrating Files"
if [ "${FILES_TO_MIGRATE[0]}" = "--all" ]; then
    print_warning "Migrating ALL files from test to production..."
    rsync -av \
        --exclude='venv/' \
        --exclude='.git/' \
        --exclude='backups/' \
        --exclude='*.log' \
        --exclude='*.pid' \
        --exclude='__pycache__/' \
        --exclude='instance/' \
        --exclude='.env' \
        "$TEST_DIR/" "$PROD_DIR/"
else
    for item in "${FILES_TO_MIGRATE[@]}"; do
        test_path="$TEST_DIR/$item"
        prod_path="$PROD_DIR/$item"
        
        if [ ! -e "$test_path" ]; then
            print_error "File not found in test: $test_path"
            continue
        fi
        
        print_warning "Migrating: $item"
        
        # Ensure parent directory exists
        mkdir -p "$(dirname "$prod_path")" 2>/dev/null || true
        
        if [ -d "$test_path" ]; then
            rsync -av --delete "$test_path/" "$prod_path/"
        else
            cp "$test_path" "$prod_path"
        fi
        
        print_success "Migrated: $item"
    done
fi

# Step 4: Commit in production
print_header "Step 4: Committing Changes (Production)"
cd "$PROD_DIR"

if [ -d ".git" ]; then
    # Ensure we're on prod branch
    CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "")
    if [ "$CURRENT_BRANCH" != "$PROD_BRANCH" ]; then
        print_warning "Not on $PROD_BRANCH branch (currently on $CURRENT_BRANCH)"
        if git show-ref --verify --quiet refs/heads/$PROD_BRANCH; then
            git checkout $PROD_BRANCH
            print_success "Switched to $PROD_BRANCH branch"
        elif git show-ref --verify --quiet refs/remotes/origin/$PROD_BRANCH; then
            git checkout -b $PROD_BRANCH origin/$PROD_BRANCH
            print_success "Created and switched to $PROD_BRANCH branch from remote"
        else
            # Create prod branch from main if it exists, otherwise from current
            if git show-ref --verify --quiet refs/heads/main; then
                git checkout main
                git checkout -b $PROD_BRANCH
                print_success "Created $PROD_BRANCH branch from main"
            else
                git checkout -b $PROD_BRANCH
                print_success "Created $PROD_BRANCH branch from $CURRENT_BRANCH"
            fi
        fi
    else
        print_success "On $PROD_BRANCH branch"
    fi
    
    # Verify we're on prod branch
    PROD_CURRENT=$(git branch --show-current)
    print_warning "Production branch: $PROD_CURRENT"
    
    # Show what changed
    if [ -n "$(git status --porcelain)" ]; then
        print_warning "Changes to commit:"
        git status --short
        
        # Create commit message
        if [ -z "$COMMIT_MSG" ]; then
            COMMIT_MSG="Migrate from test: $(IFS=','; echo "${FILES_TO_MIGRATE[*]}") - $(date '+%Y-%m-%d %H:%M:%S')"
        else
            COMMIT_MSG="Migrate from test: $COMMIT_MSG - $(date '+%Y-%m-%d %H:%M:%S')"
        fi
        
        # Commit changes
        git add -A
        git commit -m "$COMMIT_MSG"
        print_success "Changes committed to production"
        
        # Create tag if requested
        if [ "$CREATE_TAG" = true ]; then
            TAG_NAME="v$(date +%Y%m%d_%H%M%S)"
            git tag -a "$TAG_NAME" -m "Release: $COMMIT_MSG"
            print_success "Tag created: $TAG_NAME"
        fi
        
        # Push to remote if requested
        if [ "$PUSH_TO_REMOTE" = true ]; then
            REMOTE_URL=$(git remote get-url origin 2>/dev/null || echo "")
            if [ -n "$REMOTE_URL" ]; then
                print_warning "Pushing to remote: $REMOTE_URL"
                git push origin "$PROD_BRANCH" || print_warning "Push failed"
                if [ "$CREATE_TAG" = true ] && [ -n "$TAG_NAME" ]; then
                    git push origin "$TAG_NAME" || print_warning "Tag push failed"
                fi
                print_success "Pushed to remote"
            else
                print_warning "No remote repository configured"
            fi
        fi
    else
        print_warning "No changes to commit in production"
    fi
else
    print_warning "Production directory is not a git repository"
fi

# Step 5: Push test branch if it has changes
if [ "$PUSH_TO_REMOTE" = true ] && [ -d "$TEST_DIR/.git" ]; then
    print_header "Step 5: Pushing Test Branch to Remote"
    cd "$TEST_DIR"
    REMOTE_URL=$(git remote get-url origin 2>/dev/null || echo "")
    if [ -n "$REMOTE_URL" ]; then
        TEST_BRANCH=$(git branch --show-current)
        git push origin "$TEST_BRANCH" || print_warning "Test branch push failed"
        print_success "Test branch pushed to remote"
    fi
fi

# Summary
print_header "Migration Summary"
print_success "Migration completed successfully!"
echo ""
echo "Migrated from: $TEST_DIR (branch: $TEST_BRANCH)"
echo "Migrated to:   $PROD_DIR (branch: $PROD_BRANCH)"
echo "Backup location: $BACKUP_DIR"
echo ""
if [ -d "$PROD_DIR/.git" ]; then
    PROD_CURRENT=$(cd $PROD_DIR && git branch --show-current)
    echo "Production branch: $PROD_CURRENT"
    echo "Git commit: $(cd $PROD_DIR && git log -1 --oneline)"
    if [ "$PUSH_TO_REMOTE" = true ]; then
        echo "Remote sync: Completed"
    else
        echo "Remote sync: Not performed (use --push to sync)"
    fi
fi
echo ""
print_warning "Next steps:"
echo "1. Review changes in production directory"
echo "2. Ensure production is on $PROD_BRANCH branch"
echo "3. Test production: sudo systemctl restart inertia-app.service"
echo "4. Monitor logs: sudo journalctl -u inertia-app.service -f"
echo ""
print_warning "To rollback:"
echo "  Restore from: $BACKUP_DIR/backup_${DATE}_*.tar.gz"

