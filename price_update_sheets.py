#!/usr/bin/env python3
"""
Stock Price Update from Google Sheets
Runs 3 times daily on weekdays: 9:30 AM, 12:30 PM, 4:00 PM IST
Only the 4 PM run saves historical prices with date
"""

import sys
import os
import traceback

# Add the app directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, date
import pytz
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/inertia/app/logs/price_update.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def main():
    """Main function to update stock prices from Google Sheets"""
    try:
        from main import create_app
        from api.v1.cron_jobs import run_price_update_sheets
        
        app = create_app()
        
        with app.app_context():
            logger.info("=" * 70)
            logger.info("Stock Price Update - Starting")
            logger.info(f"Execution Time: {datetime.now().isoformat()}")
            logger.info("=" * 70)
            
            # Determine if this is the 4 PM run (should save historical prices)
            ist = pytz.timezone('Asia/Kolkata')
            current_time_ist = datetime.now(ist)
            hour_ist = current_time_ist.hour
            
            # 16:xx IST window persists historical closes (matches api.v1.cron_jobs.run_price_update_sheets).
            is_market_close_run = 16 <= hour_ist < 17
            
            if is_market_close_run:
                logger.info("🕓 MARKET CLOSE WINDOW (16:00–16:59 IST) - Historical prices may be persisted")
            else:
                logger.info(f"🕐 INTRADAY RUN ({hour_ist}:00 IST) - Current prices only (no dated history)")
            
            # Call the price update function
            # APIResponse.success() returns a tuple (flask_response, status_code)
            result_tuple = run_price_update_sheets()
            
            # Extract the JSON response from the tuple
            if result_tuple and isinstance(result_tuple, tuple) and len(result_tuple) >= 1:
                flask_response = result_tuple[0]
                status_code = result_tuple[1] if len(result_tuple) > 1 else 200
                
                # Get JSON data from Flask Response object
                try:
                    result_data = flask_response.get_json()
                except Exception as e:
                    logger.error(f"Failed to parse response JSON: {str(e)}")
                    result_data = {'success': False, 'message': 'Could not parse response'}
                
                if result_data and result_data.get('success'):
                    logger.info("✅ Price update completed successfully")
                    logger.info(f"Details: {result_data.get('message', 'No details')}")
                    if 'data' in result_data:
                        data = result_data['data']
                        logger.info(f"Updated: {data.get('updated_count', 0)} securities")
                        logger.info(f"Historical saved: {data.get('historical_saved_count', 0)}")
                else:
                    error_msg = result_data.get('message', 'Unknown error') if result_data else 'No result'
                    logger.error(f"❌ Price update failed: {error_msg}")
                    sys.exit(1)
            else:
                logger.error(f"❌ Price update failed: Invalid response format")
                sys.exit(1)
            
            logger.info("=" * 70)
            
    except Exception as e:
        logger.error(f"❌ Script failed with exception: {str(e)}")
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == '__main__':
    main()






