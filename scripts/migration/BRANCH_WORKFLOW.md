# Test and Production Branch Workflow

## Overview

This workflow maintains separate `test` and `prod` branches to manage code changes between test and production environments.

## Branch Structure

- **`test` branch**: Used in `/home/inertia/app_test` (test environment)
- **`prod` branch**: Used in `/home/inertia/app` (production environment)

## Initial Setup

Run the setup script to create and configure the branches:

```bash
./scripts/migration/setup_test_prod_branches.sh
```

This script will:
1. Ensure test environment is on `test` branch
2. Create `prod` branch in production (from `main` if available)
3. Push branches to remote repository
4. Set up the branch structure

## Daily Workflow

### 1. Development in Test Environment

```bash
cd /home/inertia/app_test
git checkout test
# Make your changes
git add .
git commit -m "Description of changes"
git push origin test
```

### 2. Testing

Test thoroughly in the test environment before migrating to production.

### 3. Migration to Production

Use the migration script to move changes from test to production:

```bash
# Migrate a specific file
./scripts/migration/migrate_test_to_prod.sh templates/unified_recommendations/email_preview.html --push

# Migrate a directory
./scripts/migration/migrate_test_to_prod.sh routes/ --push

# Migrate multiple files
./scripts/migration/migrate_test_to_prod.sh file1.py file2.py --push

# With custom commit message
./scripts/migration/migrate_test_to_prod.sh file.py --commit-message "Add new feature" --push
```

The migration script will:
1. Ensure test environment is on `test` branch
2. Create backup of production files
3. Copy files from test to production
4. Switch production to `prod` branch
5. Commit changes to `prod` branch
6. Optionally push to remote

### 4. Production Deployment

After migration:

```bash
cd /home/inertia/app
git checkout prod
sudo systemctl restart inertia-app.service
sudo journalctl -u inertia-app.service -f
```

## Branch Management

### Check Current Branches

```bash
# Test environment
cd /home/inertia/app_test
git branch --show-current  # Should show "test"

# Production environment
cd /home/inertia/app
git branch --show-current  # Should show "prod"
```

### Switch Branches

```bash
# In test environment
cd /home/inertia/app_test
git checkout test

# In production environment
cd /home/inertia/app
git checkout prod
```

### Sync with Remote

```bash
# Test environment
cd /home/inertia/app_test
git checkout test
git pull origin test

# Production environment
cd /home/inertia/app
git checkout prod
git pull origin prod
```

## Best Practices

1. **Always work on the correct branch**
   - Test environment should always be on `test` branch
   - Production environment should always be on `prod` branch

2. **Commit before migration**
   - Ensure all changes in test are committed before migrating
   - Use descriptive commit messages

3. **Test before production**
   - Always test thoroughly in test environment
   - Only migrate stable, tested code

4. **Use backups**
   - The migration script creates automatic backups
   - Keep backups for rollback if needed

5. **Monitor after deployment**
   - Always monitor production logs after deployment
   - Be ready to rollback if issues occur

## Rollback Procedure

If you need to rollback:

```bash
# Find the backup
ls -lh /home/inertia/backups/backup_*.tar.gz

# Restore from backup
cd /home/inertia/app
tar -xzf /home/inertia/backups/backup_YYYYMMDD_HHMMSS_*.tar.gz

# Or restore specific file
cp /home/inertia/backups/backup_YYYYMMDD_HHMMSS_filename /home/inertia/app/path/to/file

# Restart service
sudo systemctl restart inertia-app.service
```

## Troubleshooting

### Wrong Branch in Test Environment

```bash
cd /home/inertia/app_test
git checkout test
```

### Wrong Branch in Production Environment

```bash
cd /home/inertia/app
git checkout prod
```

### Branch Doesn't Exist

Run the setup script:

```bash
./scripts/migration/setup_test_prod_branches.sh
```

### Merge Conflicts

If you encounter merge conflicts during migration:

1. Resolve conflicts manually
2. Test thoroughly
3. Commit the resolved changes
4. Continue with deployment

## Migration Script Options

```bash
./scripts/migration/migrate_test_to_prod.sh [options] <file_or_directory>

Options:
  --commit-message "message"  Custom commit message
  --push                      Push to remote after migration
  --tag                       Create git tag after migration
  --all                       Migrate all files (use with caution)
```

## Examples

### Example 1: Migrate a single template file

```bash
./scripts/migration/migrate_test_to_prod.sh \
  templates/unified_recommendations/email_preview.html \
  --commit-message "Add Generate New Recommendation button" \
  --push
```

### Example 2: Migrate a route file

```bash
./scripts/migration/migrate_test_to_prod.sh \
  routes/unified_recommendations.py \
  --commit-message "Update unified recommendations route" \
  --push
```

### Example 3: Migrate multiple files

```bash
./scripts/migration/migrate_test_to_prod.sh \
  routes/main.py services/client_service.py \
  --commit-message "Update main routes and client service" \
  --push
```
