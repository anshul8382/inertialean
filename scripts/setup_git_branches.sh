#!/bin/bash
# Setup Git branches for dev/staging/prod workflow

set -e

echo "Setting up Git branches..."

# Get current branch
CURRENT_BRANCH=$(git branch --show-current)

# Create branches if they don't exist
if ! git show-ref --verify --quiet refs/heads/develop; then
    git checkout -b develop
    echo "Created develop branch"
else
    git checkout develop 2>/dev/null || true
    echo "develop branch already exists"
fi

if ! git show-ref --verify --quiet refs/heads/staging; then
    git checkout -b staging
    echo "Created staging branch"
else
    git checkout staging 2>/dev/null || true
    echo "staging branch already exists"
fi

# Return to original branch
git checkout $CURRENT_BRANCH

# Push branches to remote (if they don't exist remotely)
git push -u origin develop 2>/dev/null || echo "develop branch already exists on remote"
git push -u origin staging 2>/dev/null || echo "staging branch already exists on remote"

echo ""
echo "Git branches setup complete!"
echo ""
echo "Branch structure:"
echo "  - main/master: Production"
echo "  - staging: Pre-production testing"
echo "  - develop: Active development"
echo ""
echo "Workflow:"
echo "  1. Create feature branches from develop"
echo "  2. Merge feature branches to develop"
echo "  3. Merge develop to staging for testing"
echo "  4. Merge staging to main for production"


