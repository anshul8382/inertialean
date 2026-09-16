# Deployment Management UI Guide

## Overview
The Deployment Management UI is a web-based interface for managing code migrations from the test environment to production. It provides a user-friendly way to:
- Browse test environment files
- Select files/directories to migrate
- Execute migrations with Git integration
- View migration history and backups
- Monitor environment status

## Access
**Important:** The Deployment Management UI is **only available in the test environment** for security reasons.

### Access URL
- Test Environment: `http://localhost:5002/admin/deployment-management`
- Production Environment: Not available (access blocked)

## Features

### 1. Environment Status Dashboard
- **Production Environment Status:**
  - Directory existence
  - Server status (port 5000)
  - Database accessibility
  - Git branch and remote information

- **Test Environment Status:**
  - Directory existence
  - Server status (port 5002)
  - Database accessibility
  - Git branch and remote information

### 2. File Browser
- Browse test environment files
- Search files by name
- Select files or directories for migration
- View file sizes

### 3. Migration Interface
- **Select Files:** Choose files/directories from test environment
- **Commit Message:** Add descriptive commit message (optional)
- **Git Options:**
  - Push to remote repository (optional)
  - Create git tag for release (optional)
- **Automatic Backup:** Always creates backup before migration

### 4. Migration History
- View recent migration logs
- Check migration dates and file sizes

### 5. Backup Management
- View available backups
- Check backup dates and sizes
- Restore from backups if needed

## Usage Workflow

### Step 1: Setup Test Environment
If test directory doesn't exist:
1. Click "Setup Test Directory" button
2. Wait for setup to complete
3. Refresh the page

### Step 2: Select Files to Migrate
1. Browse files in the left panel
2. Select checkboxes for files/directories to migrate
3. Selected files appear in the "Selected Files" section

### Step 3: Configure Migration
1. Enter commit message (optional but recommended)
2. Choose Git options:
   - Check "Push to remote repository" to push changes
   - Check "Create git tag" to tag this release
3. Review selected files

### Step 4: Execute Migration
1. Click "Migrate to Production" button
2. Confirm the migration
3. Wait for migration to complete
4. Review the results

### Step 5: Post-Migration
1. Review changes in production
2. Restart production server if needed:
   ```bash
   sudo systemctl restart inertia-app.service
   ```
3. Monitor logs and test functionality

## Migration Process

When you execute a migration, the system:

1. **Creates Backup:** Automatically backs up production files
2. **Migrates Files:** Copies selected files from test to production
3. **Git Commit:** Commits changes in production with your message
4. **Git Push:** (Optional) Pushes to remote repository
5. **Git Tag:** (Optional) Creates release tag
6. **Sync Test Branch:** (Optional) Pushes test branch to remote

## Best Practices

### Before Migration
1. ✅ Test thoroughly in test environment
2. ✅ Commit all changes in test environment
3. ✅ Review selected files carefully
4. ✅ Write descriptive commit messages
5. ✅ Check for uncommitted changes in production

### During Migration
1. ✅ Migrate during low-traffic hours
2. ✅ Select specific files, not entire directories (unless necessary)
3. ✅ Use meaningful commit messages
4. ✅ Enable "Push to remote" for tracking

### After Migration
1. ✅ Verify files were migrated correctly
2. ✅ Test critical functionality in production
3. ✅ Monitor error logs
4. ✅ Restart services if needed

## Git Integration

### Automatic Git Operations
- **Test Environment:**
  - Commits changes before migration (if needed)
  - Pushes test branch to remote (optional)

- **Production Environment:**
  - Commits migrated files
  - Creates release tag (optional)
  - Pushes to remote (optional)

### Branch Strategy
- **Test Branch:** `test` or `develop`
- **Production Branch:** `main` or `master`

### Remote Repository
The system automatically detects and uses the configured Git remote. Ensure both test and production have the same remote configured.

## Troubleshooting

### "Deployment management is only available in test environment"
- Access the UI from the test server (port 5002)
- Ensure test directory exists: `/home/inertia/app_test`

### Migration Fails
- Check file permissions
- Verify test and production directories exist
- Check Git repository status
- Review error messages in the result output

### Files Not Appearing in Browser
- Refresh the file tree
- Check if files exist in test directory
- Verify file permissions

### Git Push Fails
- Check Git credentials
- Verify remote repository access
- Ensure you have push permissions

## Security

- **Access Control:** Only admin users can access deployment management
- **Environment Isolation:** UI only available in test environment
- **Automatic Backups:** All migrations create backups
- **Git Tracking:** All changes are tracked in Git

## Advanced Features

### File Comparison
- Select files and click "Compare Files"
- Shows differences between test and production versions

### Manual Git Operations
Use terminal for advanced Git operations:
```bash
# Sync from remote
cd /home/inertia/app_test
git pull origin test

# Push to remote
git push origin test
```

## Support

For issues or questions:
1. Check migration logs in `/home/inertia/backups/migration_log_*.txt`
2. Review Git history: `git log` in production directory
3. Check system logs: `sudo journalctl -u inertia-app.service`

