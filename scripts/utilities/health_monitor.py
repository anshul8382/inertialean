#!/usr/bin/env python3
"""
Health Monitor for Inertia Investment App
This script monitors the application health and can automatically restart it if needed.
"""

import requests
import subprocess
import time
import logging
import os
import sys
from datetime import datetime

# Configuration
APP_URL = "http://localhost:5000"
HEALTH_CHECK_ENDPOINT = "/"
MANAGEMENT_SCRIPT = "./manage_app_optimized.sh"
LOG_FILE = "logs/health_monitor.log"
MAX_RETRIES = 3
RETRY_DELAY = 30  # seconds

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def check_health():
    """Check if the application is healthy"""
    try:
        response = requests.get(APP_URL + HEALTH_CHECK_ENDPOINT, timeout=10)
        # Accept 302 (redirect to login) or 200 (success) as healthy
        if response.status_code in [200, 302]:
            logger.info(f"Health check passed: HTTP {response.status_code}")
            return True
        else:
            logger.warning(f"Health check failed: HTTP {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Health check error: {e}")
        return False

def restart_app():
    """Restart the application using the management script"""
    try:
        logger.info("Attempting to restart application...")
        result = subprocess.run([MANAGEMENT_SCRIPT, "restart"], 
                              capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0:
            logger.info("Application restart initiated successfully")
            return True
        else:
            logger.error(f"Restart failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("Restart command timed out")
        return False
    except Exception as e:
        logger.error(f"Restart error: {e}")
        return False

def monitor_once():
    """Perform a single health check and restart if needed"""
    if not check_health():
        logger.warning("Application is unhealthy, attempting restart...")
        if restart_app():
            # Wait for restart to complete
            time.sleep(10)
            # Check health again
            if check_health():
                logger.info("Application restarted successfully and is now healthy")
                return True
            else:
                logger.error("Application restart failed - still unhealthy")
                return False
        else:
            logger.error("Failed to restart application")
            return False
    return True

def monitor_continuous(interval=60):
    """Monitor continuously with automatic restart"""
    logger.info(f"Starting continuous monitoring (interval: {interval}s)")
    
    consecutive_failures = 0
    
    while True:
        try:
            if monitor_once():
                consecutive_failures = 0
                logger.info("Health check passed")
            else:
                consecutive_failures += 1
                logger.warning(f"Health check failed (consecutive failures: {consecutive_failures})")
                
                if consecutive_failures >= MAX_RETRIES:
                    logger.error(f"Maximum retries ({MAX_RETRIES}) reached. Stopping monitoring.")
                    break
            
            time.sleep(interval)
            
        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user")
            break
        except Exception as e:
            logger.error(f"Unexpected error in monitoring: {e}")
            time.sleep(interval)

def main():
    """Main function"""
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "check":
            # Single health check
            if check_health():
                print("OK")
                sys.exit(0)
            else:
                print("FAILED")
                sys.exit(1)
                
        elif command == "restart":
            # Restart application
            if restart_app():
                print("RESTARTED")
                sys.exit(0)
            else:
                print("RESTART_FAILED")
                sys.exit(1)
                
        elif command == "monitor":
            # Continuous monitoring
            interval = int(sys.argv[2]) if len(sys.argv) > 2 else 60
            monitor_continuous(interval)
            
        else:
            print("Usage: python3 health_monitor.py {check|restart|monitor [interval]}")
            sys.exit(1)
    else:
        # Default: single health check
        if check_health():
            print("OK")
            sys.exit(0)
        else:
            print("FAILED")
            sys.exit(1)

if __name__ == "__main__":
    main()
