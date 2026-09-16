# Test Environment Setup Guide

## Overview
The test environment provides an isolated testing setup for the Inertia Investment App, allowing you to run tests and verify functionality without affecting production or development environments.

## Environment Configuration

| Environment | Port | URL | Database | Purpose |
|-------------|------|-----|----------|---------|
| **Production** | 5000 | http://your-domain:5000 | `inertia_app2025` | Main production application |
| **Development** | 5001 | http://your-domain:5001 | `inertia_app2025_dev` | Development application |
| **Test** | 5002 | http://localhost:5002 | `inertia_app2025_test` | Testing and QA |

## Prerequisites

1. MySQL server running and accessible
2. Virtual environment set up at `/home/inertia/app/venv`
3. Required Python packages installed

## Setup Steps

### 1. Set Up Test Database

Run the test database setup script:

```bash
cd /home/inertia/app
./scripts/utilities/setup_test_database.sh
```

This script will:
- Create the `inertia_app2025_test` database
- Optionally copy schema from production database
- Optionally copy minimal test data (recommended: skip for clean test DB)

### 2. Run Database Migrations

After setting up the database, run migrations to ensure the schema is up to date:

```bash
cd /home/inertia/app
export FLASK_ENV=test
flask db upgrade
```

### 3. Configure Environment Variables

The test environment uses `.env.test` file for configuration. If it doesn't exist, create it:

```bash
cd /home/inertia/app
cp .env .env.test
# Edit .env.test to set test-specific values
```

Key test environment variables:
- `FLASK_ENV=test`
- `DB_NAME=inertia_app2025_test`
- `TESTING=True`
- `DEBUG=True`
- `WTF_CSRF_ENABLED=False` (for easier testing)

## Running the Test Server

### Start Test Server

```bash
cd /home/inertia/app
./start_test.sh
```

The test server will:
- Start on port 5002
- Use the test database (`inertia_app2025_test`)
- Enable debug mode
- Disable CSRF protection (for easier API testing)

### Stop Test Server

```bash
pkill -f "flask run.*--port 5002"
```

Or press `Ctrl+C` if running in foreground.

## Running Tests

### Run All Tests

```bash
cd /home/inertia/app
export FLASK_ENV=test
python -m pytest tests/
```

### Run Specific Test File

```bash
export FLASK_ENV=test
python -m pytest tests/test_specific_feature.py
```

### Run Tests with Verbose Output

```bash
export FLASK_ENV=test
python -m pytest tests/ -v
```

### Run Tests with Coverage

```bash
export FLASK_ENV=test
python -m pytest tests/ --cov=. --cov-report=html
```

## Configuration Details

### TestConfig Settings

The `TestConfig` class in `config.py` provides test-specific settings:

- **TESTING = True**: Enables Flask testing mode
- **DEBUG = True**: Enables debug mode for detailed error messages
- **Database**: Uses `inertia_app2025_test` database
- **CSRF Disabled**: `WTF_CSRF_ENABLED = False` for easier API testing
- **Smaller Connection Pool**: Optimized for testing with fewer connections
- **Port**: 5002 (different from prod/dev)

### Environment Variables Priority

The application loads environment variables in this order:

1. `.env.{FLASK_ENV}` (e.g., `.env.test`)
2. `.env` (fallback)
3. System environment variables

## Database Management

### Reset Test Database

To reset the test database to a clean state:

```bash
cd /home/inertia/app
mysql -u inertia_admin -p'!Nert!a2025$' -e "DROP DATABASE IF EXISTS inertia_app2025_test;"
./scripts/utilities/setup_test_database.sh
export FLASK_ENV=test
flask db upgrade
```

### Copy Schema from Production

If you need to update the test database schema from production:

```bash
mysqldump -u inertia_admin -p'!Nert!a2025$' --no-data --skip-triggers \
  inertia_app2025 | mysql -u inertia_admin -p'!Nert!a2025$' inertia_app2025_test
```

### Seed Test Data

You can create custom seed scripts for test data:

```bash
export FLASK_ENV=test
python scripts/seed_test_data.py
```

## Environment Status Check

Check the status of all environments:

```bash
cd /home/inertia/app
./check_environments.sh
```

This will show:
- Database connectivity for each environment
- Running processes
- Database table counts
- Startup commands

## Testing Workflows

### Unit Testing

```bash
export FLASK_ENV=test
python -m pytest tests/unit/ -v
```

### Integration Testing

```bash
export FLASK_ENV=test
python -m pytest tests/integration/ -v
```

### API Testing

With the test server running, you can test APIs:

```bash
# Test health endpoint
curl http://localhost:5002/health

# Test API endpoints
curl http://localhost:5002/api/v1/endpoint
```

## Troubleshooting

### Port Already in Use

If port 5002 is already in use:

```bash
# Find process using port 5002
lsof -i :5002

# Kill the process
kill -9 <PID>
```

### Database Connection Failed

1. Verify MySQL is running: `sudo systemctl status mysql`
2. Check database credentials in `.env.test`
3. Verify test database exists: `mysql -u inertia_admin -p'!Nert!a2025$' -e "SHOW DATABASES;" | grep test`

### Test Database Not Found

Run the setup script:

```bash
./scripts/utilities/setup_test_database.sh
```

### Migration Errors

```bash
export FLASK_ENV=test
flask db current  # Check current migration version
flask db upgrade  # Run migrations
flask db downgrade -1  # Rollback one migration if needed
```

## Best Practices

1. **Always use test database**: Never point tests at production or development databases
2. **Clean state**: Reset test database before running test suites
3. **Isolated tests**: Each test should be independent and not rely on other tests
4. **Mock external services**: Use mocks for external API calls in tests
5. **Use fixtures**: Leverage pytest fixtures for common test setup
6. **Test coverage**: Aim for high test coverage, especially for critical paths

## Integration with CI/CD

For CI/CD pipelines, you can set up the test environment:

```yaml
# Example GitHub Actions workflow
- name: Setup Test Database
  run: |
    mysql -u root -proot -e "CREATE DATABASE inertia_app2025_test;"
    
- name: Run Tests
  env:
    FLASK_ENV: test
    DB_NAME: inertia_app2025_test
  run: |
    flask db upgrade
    pytest tests/
```

## Additional Resources

- [Flask Testing Documentation](https://flask.palletsprojects.com/en/latest/testing/)
- [Pytest Documentation](https://docs.pytest.org/)
- [Development Environment Setup](./startup_guide.md)
- [Production Deployment Guide](./DEPLOYMENT_GUIDE.md)

