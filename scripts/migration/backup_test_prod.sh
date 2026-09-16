#!/bin/bash
#
# Backup Test and Production Directories
# Creates full backups of both test and production environments
# Usage: ./backup_test_prod.sh [--include-git] [--exclude-venv]
#

set -e

# Configuration
TEST_DIR="/home/inertia/app_test"
PROD_DIR="/home/inertia/app"
BACKUP_BASE_DIR="/home/inertia/backups"
DATE=$(date +%Y%m%d_%H%M%S)
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Options
INCLUDE_GIT=false
EXCLUDE_VENV=true

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

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --include-git)
            INCLUDE_GIT=true
            shift
            ;;
        --exclude-venv)
            EXCLUDE_VENV=true
            shift
            ;;
        --include-venv)
            EXCLUDE_VENV=false
            shift
            ;;
        *)
            print_error "Unknown option: $1"
            echo "Usage: $0 [--include-git] [--exclude-venv|--include-venv]"
            exit 1
            ;;
    esac
done

# Create backup directory with timestamp
BACKUP_DIR="$BACKUP_BASE_DIR/full_backup_${DATE}"
mkdir -p "$BACKUP_DIR"

print_header "Backup Test and Production Directories"
echo "Backup started at: $TIMESTAMP"
echo "Backup location: $BACKUP_DIR"
echo ""

# Function to get directory size
get_dir_size() {
    local dir=$1
    if [ -d "$dir" ]; then
        du -sh "$dir" 2>/dev/null | cut -f1
    else
        echo "N/A"
    fi
}

# Function to get git branch info
get_git_info() {
    local dir=$1
    if [ -d "$dir/.git" ]; then
        cd "$dir"
        BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
        COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
        echo "Branch: $BRANCH, Commit: $COMMIT"
    else
        echo "Not a git repository"
    fi
}

# Function to create backup
create_backup() {
    local source_dir=$1
    local backup_name=$2
    local description=$3
    
    if [ ! -d "$source_dir" ]; then
        print_error "$description directory not found: $source_dir"
        return 1
    fi
    
    print_header "Backing up $description"
    echo "Source: $source_dir"
    echo "Size: $(get_dir_size "$source_dir")"
    echo "Git info: $(get_git_info "$source_dir")"
    
    # Build tar exclude options
    EXCLUDE_OPTIONS=""
    if [ "$EXCLUDE_VENV" = true ]; then
        EXCLUDE_OPTIONS="$EXCLUDE_OPTIONS --exclude='venv' --exclude='*/venv'"
    fi
    
    if [ "$INCLUDE_GIT" = false ]; then
        EXCLUDE_OPTIONS="$EXCLUDE_OPTIONS --exclude='.git' --exclude='*/.git'"
    fi
    
    EXCLUDE_OPTIONS="$EXCLUDE_OPTIONS --exclude='__pycache__' --exclude='*/__pycache__'"
    EXCLUDE_OPTIONS="$EXCLUDE_OPTIONS --exclude='*.pyc' --exclude='*.pyo'"
    EXCLUDE_OPTIONS="$EXCLUDE_OPTIONS --exclude='*.log' --exclude='*.pid'"
    EXCLUDE_OPTIONS="$EXCLUDE_OPTIONS --exclude='instance' --exclude='*/instance'"
    EXCLUDE_OPTIONS="$EXCLUDE_OPTIONS --exclude='backups' --exclude='*/backups'"
    EXCLUDE_OPTIONS="$EXCLUDE_OPTIONS --exclude='.env' --exclude='*.swp'"
    EXCLUDE_OPTIONS="$EXCLUDE_OPTIONS --exclude='*.swo' --exclude='*~'"
    
    BACKUP_FILE="$BACKUP_DIR/${backup_name}_${DATE}.tar.gz"
    
    print_warning "Creating backup archive..."
    
    # Create tar with exclusions
    eval "tar -czf \"$BACKUP_FILE\" $EXCLUDE_OPTIONS -C \"$(dirname "$source_dir")\" \"$(basename "$source_dir")\" 2>/dev/null"
    
    if [ $? -eq 0 ]; then
        BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
        print_success "Backup created: $backup_name"
        echo "  File: $(basename "$BACKUP_FILE")"
        echo "  Size: $BACKUP_SIZE"
        echo "  Location: $BACKUP_FILE"
        return 0
    else
        print_error "Failed to create backup for $description"
        return 1
    fi
}

# Step 1: Backup Test Directory
print_header "Step 1: Backing up Test Environment"
TEST_BACKUP_SUCCESS=false
if create_backup "$TEST_DIR" "test_backup" "Test"; then
    TEST_BACKUP_SUCCESS=true
fi
echo ""

# Step 2: Backup Production Directory
print_header "Step 2: Backing up Production Environment"
PROD_BACKUP_SUCCESS=false
if create_backup "$PROD_DIR" "prod_backup" "Production"; then
    PROD_BACKUP_SUCCESS=true
fi
echo ""

# Step 3: Create backup manifest
print_header "Step 3: Creating Backup Manifest"
MANIFEST_FILE="$BACKUP_DIR/backup_manifest_${DATE}.txt"

cat > "$MANIFEST_FILE" << EOF
========================================
Full System Backup Manifest
========================================
Backup Date: $TIMESTAMP
Backup Directory: $BACKUP_DIR

Test Environment:
  Directory: $TEST_DIR
  Backup Status: $([ "$TEST_BACKUP_SUCCESS" = true ] && echo "SUCCESS" || echo "FAILED")
  Git Info: $(get_git_info "$TEST_DIR")
  Directory Size: $(get_dir_size "$TEST_DIR")
  Backup File: $([ "$TEST_BACKUP_SUCCESS" = true ] && echo "test_backup_${DATE}.tar.gz" || echo "N/A")
  Backup Size: $([ "$TEST_BACKUP_SUCCESS" = true ] && du -h "$BACKUP_DIR/test_backup_${DATE}.tar.gz" 2>/dev/null | cut -f1 || echo "N/A")

Production Environment:
  Directory: $PROD_DIR
  Backup Status: $([ "$PROD_BACKUP_SUCCESS" = true ] && echo "SUCCESS" || echo "FAILED")
  Git Info: $(get_git_info "$PROD_DIR")
  Directory Size: $(get_dir_size "$PROD_DIR")
  Backup File: $([ "$PROD_BACKUP_SUCCESS" = true ] && echo "prod_backup_${DATE}.tar.gz" || echo "N/A")
  Backup Size: $([ "$PROD_BACKUP_SUCCESS" = true ] && du -h "$BACKUP_DIR/prod_backup_${DATE}.tar.gz" 2>/dev/null | cut -f1 || echo "N/A")

Backup Options:
  Include Git: $INCLUDE_GIT
  Exclude Venv: $EXCLUDE_VENV

Total Backup Size: $(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)

Files in Backup:
EOF

ls -lh "$BACKUP_DIR" >> "$MANIFEST_FILE" 2>/dev/null || true

print_success "Manifest created: $(basename "$MANIFEST_FILE")"
echo ""

# Step 4: Create restore instructions
print_header "Step 4: Creating Restore Instructions"
RESTORE_FILE="$BACKUP_DIR/RESTORE_INSTRUCTIONS.txt"

cat > "$RESTORE_FILE" << 'EOF'
========================================
Restore Instructions
========================================

To restore from this backup:

1. Stop the service (if running):
   sudo systemctl stop inertia-app.service

2. Restore Test Environment:
   cd /home/inertia
   tar -xzf backups/full_backup_YYYYMMDD_HHMMSS/test_backup_YYYYMMDD_HHMMSS.tar.gz

3. Restore Production Environment:
   cd /home/inertia
   tar -xzf backups/full_backup_YYYYMMDD_HHMMSS/prod_backup_YYYYMMDD_HHMMSS.tar.gz

4. Restart the service:
   sudo systemctl start inertia-app.service

5. Verify:
   sudo systemctl status inertia-app.service
   sudo journalctl -u inertia-app.service -f

Note: Replace YYYYMMDD_HHMMSS with the actual backup timestamp.
EOF

print_success "Restore instructions created"
echo ""

# Summary
print_header "Backup Summary"
if [ "$TEST_BACKUP_SUCCESS" = true ] && [ "$PROD_BACKUP_SUCCESS" = true ]; then
    print_success "✅ Both backups completed successfully!"
elif [ "$TEST_BACKUP_SUCCESS" = true ]; then
    print_warning "⚠️  Test backup succeeded, but production backup failed"
elif [ "$PROD_BACKUP_SUCCESS" = true ]; then
    print_warning "⚠️  Production backup succeeded, but test backup failed"
else
    print_error "❌ Both backups failed!"
    exit 1
fi

echo ""
echo "Backup Location: $BACKUP_DIR"
echo "Total Size: $(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)"
echo ""
echo "Files created:"
ls -lh "$BACKUP_DIR" | tail -n +2 | awk '{print "  " $9 " (" $5 ")"}'
echo ""
print_warning "Next steps:"
echo "  1. Verify backup files exist and have reasonable sizes"
echo "  2. Test restore procedure in a safe location if needed"
echo "  3. Store backup in a safe location (consider off-site backup)"
echo ""
print_success "Backup completed at: $(date '+%Y-%m-%d %H:%M:%S')"
