#!/usr/bin/env python3
"""
Fix NIFTY Prices Script
Fixes incorrect NIFTY prices for a specific date range by fetching from Google Sheets
"""

import sys
import os
import logging
from datetime import datetime, date, timedelta

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
        logging.FileHandler('logs/nifty_fix.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def fix_nifty_prices_for_range(start_date: date, end_date: date):
    """
    Fix NIFTY prices for a specific date range by fetching from Google Sheets
    
    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
    """
    try:
        logger.info(f"Starting NIFTY price fix for range: {start_date} to {end_date}")
        
        app = create_app()
        
        with app.app_context():
            # Ensure Nifty benchmark exists
            benchmark = Benchmark.query.filter_by(id=1).first()
            if not benchmark:
                logger.error("NIFTY 50 benchmark not found!")
                return {
                    'success': False,
                    'error': 'NIFTY 50 benchmark not found'
                }
            
            logger.info(f"Found benchmark: {benchmark.name} (ID: {benchmark.id})")
            
            # Step 1: Delete incorrect records in the date range
            logger.info(f"Deleting existing records for date range {start_date} to {end_date}...")
            deleted_count = BenchmarkData.query.filter(
                BenchmarkData.benchmark_id == benchmark.id,
                BenchmarkData.date >= start_date,
                BenchmarkData.date <= end_date
            ).delete(synchronize_session=False)
            
            db.session.commit()
            logger.info(f"Deleted {deleted_count} existing records")
            
            # Step 2: Fetch data from Google Sheets
            logger.info("Fetching data from Google Sheets...")
            
            try:
                # Setup Google Sheets
                scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
                creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
                client = gspread.authorize(creds)
                
                # Open spreadsheet
                spreadsheet_key = "1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM"
                spreadsheet = client.open_by_key(spreadsheet_key)
                worksheet = spreadsheet.worksheet('Nifty')
                
                # Get all Nifty data
                data = worksheet.get_all_records()
                logger.info(f"Fetched {len(data)} rows from Google Sheets")
                
                if not data:
                    logger.error("No data found in Google Sheets")
                    return {
                        'success': False,
                        'error': 'No data found in Google Sheets'
                    }
                
                # Step 3: Process and filter data for the date range
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
                            
                            # Filter for the target date range
                            if start_date <= date_obj <= end_date:
                                valid_entries.append({
                                    'date': date_obj,
                                    'price': price_value
                                })
                                
                        except ValueError as e:
                            logger.warning(f"Could not parse date '{date_str}': {e}")
                            continue
                
                logger.info(f"Found {len(valid_entries)} valid entries in date range")
                
                if not valid_entries:
                    logger.warning("No valid entries found in the specified date range")
                    return {
                        'success': False,
                        'error': 'No valid entries found in Google Sheets for the specified date range',
                        'deleted_count': deleted_count
                    }
                
                # Step 4: Insert correct data
                logger.info(f"Inserting {len(valid_entries)} new records...")
                inserted_count = 0
                updated_count = 0
                
                for entry in valid_entries:
                    # Check if record already exists (shouldn't, but just in case)
                    existing = BenchmarkData.query.filter_by(
                        benchmark_id=benchmark.id,
                        date=entry['date']
                    ).first()
                    
                    if existing:
                        existing.price = entry['price']
                        existing.updated_at = datetime.utcnow()
                        updated_count += 1
                        logger.debug(f"Updated price for {entry['date']}: ₹{entry['price']:,.2f}")
                    else:
                        new_data = BenchmarkData(
                            benchmark_id=benchmark.id,
                            date=entry['date'],
                            price=entry['price'],
                            created_at=datetime.utcnow()
                        )
                        db.session.add(new_data)
                        inserted_count += 1
                        logger.debug(f"Inserted price for {entry['date']}: ₹{entry['price']:,.2f}")
                
                db.session.commit()
                
                logger.info(f"✅ Successfully fixed NIFTY prices:")
                logger.info(f"   Deleted: {deleted_count} records")
                logger.info(f"   Inserted: {inserted_count} records")
                logger.info(f"   Updated: {updated_count} records")
                logger.info(f"   Total processed: {len(valid_entries)} records")
                
                # Show sample of inserted data
                if valid_entries:
                    logger.info(f"\nSample of corrected data:")
                    for entry in sorted(valid_entries, key=lambda x: x['date'], reverse=True)[:5]:
                        logger.info(f"   {entry['date']}: ₹{entry['price']:,.2f}")
                
                return {
                    'success': True,
                    'deleted_count': deleted_count,
                    'inserted_count': inserted_count,
                    'updated_count': updated_count,
                    'total_processed': len(valid_entries),
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()
                }
                
            except Exception as e:
                logger.error(f"Error fetching from Google Sheets: {str(e)}")
                return {
                    'success': False,
                    'error': f'Google Sheets error: {str(e)}',
                    'deleted_count': deleted_count
                }
            
    except Exception as e:
        logger.error(f"❌ NIFTY price fix failed: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

if __name__ == "__main__":
    print("=== NIFTY Price Fix Script ===")
    print(f"Started at: {datetime.now()}")
    
    # Fix dates: October 22, 2025 to November 21, 2025
    start_date = date(2025, 10, 22)
    end_date = date(2025, 11, 21)
    
    print(f"Fixing NIFTY prices for date range: {start_date} to {end_date}")
    
    result = fix_nifty_prices_for_range(start_date, end_date)
    
    if result['success']:
        print(f"\n✅ Script completed successfully")
        print(f"   Deleted: {result['deleted_count']} records")
        print(f"   Inserted: {result['inserted_count']} records")
        print(f"   Updated: {result['updated_count']} records")
        print(f"   Total processed: {result['total_processed']} records")
    else:
        print(f"\n❌ Script failed")
        if 'error' in result:
            print(f"   Error: {result['error']}")
        if 'deleted_count' in result:
            print(f"   Deleted: {result['deleted_count']} records (before error)")
    
    print(f"\nCompleted at: {datetime.now()}")

