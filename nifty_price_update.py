#!/usr/bin/env python3
"""
Nifty Price Update Script
Updates Nifty benchmark price from Google Sheets
Runs 3 times daily on weekdays: 9:30 AM, 12:30 PM, 4:00 PM IST
Only the 4 PM run saves historical prices with date
"""

import sys
import os
import logging
from datetime import datetime, date
import pytz

# Add the app directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from run import create_app
from models import db, Benchmark, BenchmarkData
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/nifty_update.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def update_nifty_price():
    """Update Nifty benchmark price from Google Sheets"""
    try:
        logger.info("Starting Nifty price update...")
        
        # Determine if this is the 4 PM run (should save historical prices)
        ist = pytz.timezone('Asia/Kolkata')
        current_time_ist = datetime.now(ist)
        hour_ist = current_time_ist.hour
        
        # 16:xx IST window = market close (matches api.v1.cron_jobs.run_price_update_sheets)
        is_market_close_run = 16 <= hour_ist < 17
        
        if is_market_close_run:
            logger.info("🕓 MARKET CLOSE RUN (4 PM IST) - Will save historical prices")
        else:
            logger.info(f"🕐 INTRADAY RUN ({hour_ist}:00 IST) - Will update current price only")
        
        app = create_app()
        
        with app.app_context():
            # Ensure Nifty benchmark exists
            benchmark = Benchmark.query.filter_by(id=1).first()
            if not benchmark:
                logger.info("Creating NIFTY 50 benchmark...")
                benchmark = Benchmark(
                    id=1,
                    name='NIFTY 50',
                    symbol='^NSEI',
                    description='Nifty 50 Index - Benchmark for Indian equity market',
                    is_active=True
                )
                db.session.add(benchmark)
                db.session.commit()
                logger.info("Created NIFTY 50 benchmark")
            else:
                logger.info(f"Found benchmark: {benchmark.name}")
            
            methods_tried = []
            method_used = None
            errors = []
            
            # Method 1: Try Google Sheets (primary method)
            try:
                logger.info("Attempting to fetch Nifty price from Google Sheets...")
                
                # Setup Google Sheets
                scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
                creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
                client = gspread.authorize(creds)
                
                # Open spreadsheet
                spreadsheet_key = "1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM"
                spreadsheet = client.open_by_key(spreadsheet_key)
                worksheet = spreadsheet.worksheet('Nifty')
                
                # Get the latest Nifty data
                data = worksheet.get_all_records()
                if data:
                    # Find the most recent valid entry by parsing dates
                    # Process all rows and find the one with the latest date
                    valid_entries = []
                    for row in data:
                        date_str = row.get('Date', '')
                        # Try to find a valid price column (handle #VALUE! errors)
                        price_value = None
                        for key, value in row.items():
                            if key != 'Date' and value and str(value) not in ['#N/A', '#VALUE!', '#N/A N/A', '#NA', '']:
                                try:
                                    price_value = float(value)
                                    if price_value > 0:
                                        break
                                except (ValueError, TypeError):
                                    continue
                        
                        if price_value and price_value > 0 and date_str:
                            # Parse date
                            try:
                                # Handle different date formats
                                if '/' in date_str:
                                    date_obj = datetime.strptime(date_str, '%m/%d/%Y').date()
                                elif '-' in date_str:
                                    # Try multiple date formats
                                    try:
                                        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                                    except ValueError:
                                        date_obj = datetime.strptime(date_str, '%d-%m-%Y').date()
                                else:
                                    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                                
                                valid_entries.append({
                                    'date': date_obj,
                                    'price': price_value,
                                    'row': row
                                })
                            except ValueError as e:
                                logger.warning(f"Could not parse date '{date_str}': {e}")
                                continue
                    
                    if valid_entries:
                        # Sort by date descending to get most recent (latest date first)
                        valid_entries.sort(key=lambda x: x['date'], reverse=True)
                        latest_entry = valid_entries[0]
                        price_value = latest_entry['price']
                        date_value = latest_entry['date']
                        latest_row = latest_entry['row']
                        
                        logger.info(f"Found {len(valid_entries)} valid entries")
                        logger.info(f"Most recent entry: Date={date_value}, Price={price_value}")
                        logger.info(f"All entry dates (sorted): {[e['date'] for e in valid_entries[:5]]}")
                    else:
                        price_value = None
                        date_value = None
                        latest_row = None
                        logger.error("No valid entries found in Google Sheets")
                    
                    if price_value and price_value > 0:
                        logger.info(f"Using price: ₹{price_value:,.2f} for date: {date_value}")
                        
                        # For intraday runs (not 4 PM), update the latest price record without creating new historical entries
                        # For 4 PM run, save historical price with date
                        today = date.today()
                        
                        if is_market_close_run:
                            # Market close run: Save historical price with date
                            data_date = date_value if date_value else today
                            
                            # Check if we already have data for this date
                            existing_data = BenchmarkData.query.filter_by(
                                benchmark_id=benchmark.id,
                                date=data_date
                            ).first()
                            
                            if existing_data:
                                old_price = existing_data.price
                                existing_data.price = price_value
                                existing_data.updated_at = datetime.utcnow()
                                logger.info(f"Updated existing Nifty price for {data_date}: {old_price} → {price_value}")
                            else:
                                new_data = BenchmarkData(
                                    benchmark_id=benchmark.id,
                                    date=data_date,
                                    price=price_value,
                                    created_at=datetime.utcnow()
                                )
                                db.session.add(new_data)
                                logger.info(f"Added new Nifty price for {data_date}: {price_value}")
                            
                            db.session.commit()
                            method_used = 'Google Sheets'
                            methods_tried.append('Google Sheets: Success')
                            
                            logger.info(f"✅ Nifty price updated successfully: ₹{price_value:,.2f} for date {data_date} (via {method_used}) - Historical price saved")
                            print(f"✅ Nifty price updated successfully: ₹{price_value:,.2f} for date {data_date}")
                            
                            return {
                                'success': True,
                                'nifty_price': price_value,
                                'method_used': method_used,
                                'date': data_date.isoformat(),
                                'historical_saved': True
                            }
                        else:
                            # Intraday run: Update latest price record (most recent date)
                            latest_data = BenchmarkData.query.filter_by(
                                benchmark_id=benchmark.id
                            ).order_by(BenchmarkData.date.desc()).first()
                            
                            if latest_data:
                                old_price = latest_data.price
                                latest_data.price = price_value
                                latest_data.updated_at = datetime.utcnow()
                                logger.info(f"Updated latest Nifty price: {old_price} → {price_value} (intraday update)")
                            else:
                                # No existing data, create one for today
                                new_data = BenchmarkData(
                                    benchmark_id=benchmark.id,
                                    date=today,
                                    price=price_value,
                                    created_at=datetime.utcnow()
                                )
                                db.session.add(new_data)
                                logger.info(f"Created new Nifty price entry for {today}: {price_value}")
                            
                            db.session.commit()
                            method_used = 'Google Sheets'
                            methods_tried.append('Google Sheets: Success')
                            
                            logger.info(f"✅ Nifty price updated successfully: ₹{price_value:,.2f} (intraday update, no historical save)")
                            print(f"✅ Nifty price updated successfully: ₹{price_value:,.2f} (intraday)")
                            
                            return {
                                'success': True,
                                'nifty_price': price_value,
                                'method_used': method_used,
                                'date': today.isoformat(),
                                'historical_saved': False
                            }
                    else:
                        methods_tried.append('Google Sheets: No valid price found')
                        errors.append('No valid price found in Google Sheets')
                        logger.error("No valid price found in Google Sheets")
                else:
                    methods_tried.append('Google Sheets: No data found')
                    errors.append('No data found in Google Sheets')
                    logger.error("No data found in Google Sheets")
                    
            except Exception as e:
                methods_tried.append(f'Google Sheets: Error - {str(e)}')
                errors.append(f'Google Sheets error: {str(e)}')
                logger.error(f"Google Sheets error: {str(e)}")
            
            # If Google Sheets failed, log error
            logger.error(f"❌ Failed to update Nifty price. Methods tried: {', '.join(methods_tried)}")
            print(f"❌ Failed to update Nifty price. Methods tried: {', '.join(methods_tried)}")
            
            return {
                'success': False,
                'methods_tried': methods_tried,
                'errors': errors
            }
            
    except Exception as e:
        logger.error(f"❌ Nifty price update failed: {str(e)}")
        print(f"❌ Nifty price update failed: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

if __name__ == "__main__":
    print("=== Nifty Price Update Script ===")
    print(f"Started at: {datetime.now()}")
    
    result = update_nifty_price()
    
    if result['success']:
        print(f"✅ Script completed successfully")
        print(f"   Price: ₹{result['nifty_price']:,.2f}")
        print(f"   Method: {result['method_used']}")
        print(f"   Date: {result['date']}")
    else:
        print(f"❌ Script failed")
        if 'error' in result:
            print(f"   Error: {result['error']}")
        if 'methods_tried' in result:
            print(f"   Methods tried: {', '.join(result['methods_tried'])}")
    
    print(f"Completed at: {datetime.now()}")

