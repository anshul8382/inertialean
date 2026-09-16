# Worker Timeout Optimization Guide

## Problem Summary

The Inertia Investment App was experiencing worker timeout issues causing 500 Internal Server Errors on several pages. The main issues were:

1. **Over-provisioned Workers**: The original configuration was creating 5 workers on a 2-core system, causing resource contention
2. **No Timeout Settings**: Missing timeout configurations led to worker hangs
3. **Memory Pressure**: High memory usage with swap being fully utilized
4. **No Health Monitoring**: Lack of monitoring meant issues weren't detected early

## Solutions Implemented

### 1. Optimized Gunicorn Configuration

**File**: `gunicorn_config.py`

**Key Changes**:
- Reduced workers from 5 to 2 for 2-core system
- Added timeout settings (120s request timeout, 30s graceful timeout)
- Reduced max_requests from 1000 to 500 to prevent memory leaks
- Added worker_tmp_dir to use RAM for temporary files
- Added comprehensive logging and monitoring hooks

**Production Configuration**: `gunicorn_config_production.py`
- Even more conservative settings for production
- Reduced max_requests to 300
- Added security limits
- Optimized for stability over performance

### 2. Application Management Script

**File**: `manage_app_optimized.sh`

**Features**:
- Health checking with HTTP status validation
- Graceful shutdown with timeout handling
- Resource monitoring (memory, disk usage)
- Log management and cleanup
- Continuous monitoring mode
- Colored output for better visibility

**Usage**:
```bash
./manage_app_optimized.sh start          # Start in development mode
./manage_app_optimized.sh start production # Start in production mode
./manage_app_optimized.sh status         # Show current status
./manage_app_optimized.sh health         # Check health
./manage_app_optimized.sh monitor        # Continuous monitoring
./manage_app_optimized.sh logs 100       # Show recent logs
```

### 3. Health Monitoring System

**File**: `health_monitor.py`

**Features**:
- Automated health checks
- Automatic restart on failure
- Configurable retry logic
- Logging to file and console
- Can be run as cron job

**Usage**:
```bash
python3 health_monitor.py check          # Single health check
python3 health_monitor.py restart        # Restart application
python3 health_monitor.py monitor 60     # Continuous monitoring (60s interval)
```

## Current System Status

### Resource Allocation
- **CPU**: 2 cores
- **RAM**: 3.6GB total, ~1GB available
- **Workers**: 2 (optimized for system capacity)
- **Memory Usage**: ~200MB total for workers

### Performance Improvements
- Reduced worker count prevents resource contention
- Timeout settings prevent worker hangs
- Memory management prevents leaks
- Health monitoring provides early detection

## Monitoring and Maintenance

### Daily Monitoring
```bash
# Check application status
./manage_app_optimized.sh status

# Monitor health continuously
./manage_app_optimized.sh monitor

# Check logs for issues
./manage_app_optimized.sh logs 50
```

### Automated Monitoring (Cron Job)
Add to crontab for automated health checks:
```bash
# Check health every 5 minutes
*/5 * * * * cd /home/inertia/app && python3 health_monitor.py check

# Clean logs weekly
0 2 * * 0 cd /home/inertia/app && ./manage_app_optimized.sh clean
```

### Troubleshooting

#### If Application Becomes Unresponsive
1. Check status: `./manage_app_optimized.sh status`
2. Check logs: `./manage_app_optimized.sh logs 100`
3. Restart: `./manage_app_optimized.sh restart`
4. If still issues, restart in production mode: `./manage_app_optimized.sh restart production`

#### If Workers Keep Timing Out
1. Check system resources: `free -h && df -h`
2. Reduce worker count further if needed
3. Increase timeout settings
4. Check for memory leaks in application code

#### If Database Connection Issues
1. Check MySQL status: `systemctl status mysql`
2. Check connection pool settings
3. Verify database credentials in `main.py`

## Best Practices

### For Development
- Use `gunicorn_config.py` with 2 workers
- Monitor with `./manage_app_optimized.sh monitor`
- Check logs regularly for errors

### For Production
- Use `gunicorn_config_production.py` with conservative settings
- Set up automated health monitoring
- Implement log rotation
- Monitor system resources

### Performance Tuning
- Monitor memory usage and adjust worker count
- Use `worker_tmp_dir = "/dev/shm"` for better I/O performance
- Implement database connection pooling
- Consider using async workers for I/O-bound operations

## Future Improvements

1. **Load Balancing**: Consider using nginx as reverse proxy
2. **Process Management**: Implement systemd service for better process management
3. **Metrics Collection**: Add Prometheus/Grafana for detailed metrics
4. **Auto-scaling**: Implement auto-scaling based on load
5. **Database Optimization**: Add database connection pooling and query optimization

## Emergency Procedures

### Complete System Restart
```bash
# Stop all processes
./manage_app_optimized.sh stop
pkill -f gunicorn

# Clear any stuck processes
pkill -9 -f gunicorn

# Restart in production mode
./manage_app_optimized.sh start production

# Verify health
./manage_app_optimized.sh health
```

### Database Recovery
```bash
# Check database status
mysql -u inertia_admin -p'!Nert!a2025$' -e "SELECT 1;"

# Restart MySQL if needed
systemctl restart mysql

# Restart application
./manage_app_optimized.sh restart
```

## Contact Information

For issues or questions about the optimization:
- Check logs first: `./manage_app_optimized.sh logs`
- Monitor system resources: `./manage_app_optimized.sh status`
- Use health monitor: `python3 health_monitor.py check`

---

**Last Updated**: August 27, 2025
**Optimization Version**: 1.0
