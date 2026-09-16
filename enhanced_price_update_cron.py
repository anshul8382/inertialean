#!/usr/bin/env python3
"""
Enhanced Price Update Cron Job
Updates current prices from Google Sheets AND saves to historical price table
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import create_app
from models import db, Security, HistoricalPrice
from datetime import datetime, date
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def update_prices_with_history():
    """
    Update current prices from Google Sheets
    AND save to historical_price table for future analysis
    """
    app = create_app()
    
    with app.app_context():
        logger.info("=== ENHANCED PRICE UPDATE (WITH HISTORICAL STORAGE) ===")
        logger.info(f"Started at: {datetime.now()}")
        
        try:
            # Setup Google Sheets
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
            client = gspread.authorize(creds)
            
            # Open spreadsheet
            spreadsheet_key = "1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM"
            spreadsheet = client.open_by_key(spreadsheet_key)
            worksheet = spreadsheet.worksheet('Prices')
            
            # Get all data
            data = worksheet.get_all_records()
            logger.info(f"Read {len(data)} rows from Google Sheets")
            
            updated_count = 0
            historical_saved_count = 0
            skipped_count = 0
            errors = []
            
            today = date.today()
            
            for row in data:
                try:
                    symbol = row.get('Symbol', '').strip().upper()
                    price = row.get('Price')
                    
                    if not symbol or not price:
                        skipped_count += 1
                        continue
                    
                    # Validate price
                    if isinstance(price, str):
                        price = price.strip()
                        # Skip invalid values
                        if price in ['#N/A', '#N/A N/A', '#NA', '-1.#IND', '-1.#QNAN', 
                                    '-#N/A N/A', '-#QNAN', '1.#IND', '1.#QNAN', '#DIV/0!', 
                                    '#REF!', '#NAME?', '#NULL!', '#VALUE!', '#NUM!', 'N/A', '']:
                            skipped_count += 1
                            continue
                    
                    # Convert to float
                    try:
                        price_float = float(price)
                    except (ValueError, TypeError):
                        skipped_count += 1
                        continue
                    
                    # Validate range
                    if price_float <= 0 or price_float > 1000000:
                        skipped_count += 1
                        continue
                    
                    # Find security in database
                    security = Security.query.filter_by(symbol=symbol).first()
                    
                    if not security:
                        skipped_count += 1
                        continue
                    
                    # Update current price
                    security.current_price = price_float
                    security.last_updated = datetime.utcnow()
                    updated_count += 1
                    
                    # Save to historical_price table
                    try:
                        # Check if historical price already exists for today
                        existing_historical = HistoricalPrice.query.filter_by(
                            security_id=security.id,
                            date=today
                        ).first()
                        
                        if existing_historical:
                            # Update if source is cron (same or lower priority)
                            if existing_historical.source in ['cron', 'googlefinance']:
                                existing_historical.close_price = price_float
                                existing_historical.source = 'cron'
                                existing_historical.confidence = 0.99
                                existing_historical.updated_at = datetime.utcnow()
                        else:
                            # Create new historical price record
                            historical_price = HistoricalPrice(
                                security_id=security.id,
                                date=today,
                                close_price=price_float,
                                source='cron',
                                confidence=0.99  # High confidence (from official source)
                            )
                            db.session.add(historical_price)
                            historical_saved_count += 1
                        
                    except Exception as hist_error:
                        logger.warning(f"Could not save historical price for {symbol}: {str(hist_error)}")
                        # Continue anyway - at least current price is updated
                    
                except Exception as e:
                    errors.append(f"{symbol}: {str(e)}")
                    continue
            
            # Commit all changes
            db.session.commit()
            
            logger.info("=== UPDATE COMPLETE ===")
            logger.info(f"Current prices updated: {updated_count}")
            logger.info(f"Historical prices saved: {historical_saved_count}")
            logger.info(f"Skipped: {skipped_count}")
            logger.info(f"Errors: {len(errors)}")
            
            if errors:
                logger.warning(f"Errors encountered: {errors[:5]}")  # Show first 5 errors
            
            return {
                'success': True,
                'updated': updated_count,
                'historical_saved': historical_saved_count,
                'skipped': skipped_count,
                'errors': len(errors)
            }
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Fatal error in price update: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }


if __name__ == '__main__':
    result = update_prices_with_history()
    print(f"\n{'✅ SUCCESS' if result.get('success') else '❌ FAILED'}")
    print(f"Updated: {result.get('updated', 0)}")
    print(f"Historical saved: {result.get('historical_saved', 0)}")
    print(f"Skipped: {result.get('skipped', 0)}")
    if result.get('error'):
        print(f"Error: {result['error']}")


