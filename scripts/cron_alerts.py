#!/usr/bin/env python3
"""
Cron Job for Automated Alert System
Runs alert checks at regular intervals
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alert_system import AlertSystem
import logging
import schedule
import time
from datetime import datetime

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/inertia/app/logs/alert_system.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def run_alert_checks():
    """Run all alert checks"""
    try:
        logger.info("🔄 Starting scheduled alert checks...")
        
        alert_system = AlertSystem()
        total_alerts = alert_system.run_all_checks()
        
        logger.info(f"✅ Scheduled alert checks complete - {total_alerts} alerts sent")
        
        return total_alerts
        
    except Exception as e:
        logger.error(f"❌ Error in scheduled alert checks: {e}")
        import traceback
        traceback.print_exc()
        return 0

def main():
    """Main function to run cron job"""
    logger.info("🚨 Starting Corporate Action Alert Cron Job")
    
    # Schedule alert checks
    # Run every hour during market hours (9 AM to 4 PM)
    schedule.every().hour.at(":00").do(run_alert_checks)
    
    # Run additional checks at market open and close
    schedule.every().day.at("09:15").do(run_alert_checks)  # Market open
    schedule.every().day.at("15:30").do(run_alert_checks)  # Market close
    
    # Run comprehensive check once daily
    schedule.every().day.at("18:00").do(run_alert_checks)  # End of day
    
    logger.info("📅 Alert schedule configured:")
    logger.info("   - Every hour during market hours")
    logger.info("   - Market open (9:15 AM)")
    logger.info("   - Market close (3:30 PM)")
    logger.info("   - End of day (6:00 PM)")
    
    # Run initial check
    logger.info("🔄 Running initial alert check...")
    initial_alerts = run_alert_checks()
    logger.info(f"Initial check complete - {initial_alerts} alerts sent")
    
    # Keep running
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute

if __name__ == "__main__":
    main()





