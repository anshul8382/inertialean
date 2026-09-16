#!/usr/bin/env python3
"""
Historical Price Service
Provides historical stock prices using Google Sheets with GOOGLEFINANCE formulas
"""

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, date
import logging
from models import Transaction, BenchmarkData, Security
import pandas as pd

from services.google_sheets_historical_price import _sanitize_symbol

logger = logging.getLogger(__name__)

class HistoricalPriceService:
    """Service to get historical stock prices"""
    
    SPREADSHEET_KEY = '1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM'
    HISTORICAL_SHEET_NAME = 'HistoricalPrices'  # You'll create this
    
    @staticmethod
    def get_historical_price(security, target_date, client_id=None):
        """
        Get historical price for a security on a specific date
        Uses waterfall approach: Transaction > Google Sheets > Nifty Proxy > Current
        
        Args:
            security: Security object
            target_date: date object
            client_id: Optional client ID to filter transactions
            
        Returns:
            dict with price, source, accuracy, date
        """
        try:
            # PRIORITY 1: Transaction Price (BEST - 100% accurate)
            transaction_price = HistoricalPriceService._get_price_from_transaction(
                security, target_date, client_id
            )
            if transaction_price:
                return transaction_price
            
            # PRIORITY 2: Google Sheets Formula (GOOD - 95% accurate)
            sheets_price = HistoricalPriceService._get_price_from_google_sheets(
                security, target_date
            )
            if sheets_price:
                return sheets_price
            
            # PRIORITY 3: Nifty Proxy (ACCEPTABLE - 75% accurate)
            nifty_price = HistoricalPriceService._get_price_from_nifty_proxy(
                security, target_date
            )
            if nifty_price:
                return nifty_price
            
            # PRIORITY 4: Current Price (FALLBACK - accuracy varies)
            if security.current_price:
                return {
                    'price': float(security.current_price),
                    'source': 'current_price',
                    'date': datetime.now().date(),
                    'accuracy': 'approximate',
                    'confidence': 0.5,
                    'note': 'Using current price (no historical data available)'
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting historical price: {str(e)}")
            return None
    
    @staticmethod
    def _get_price_from_transaction(security, target_date, client_id=None):
        """Get price from transaction closest to target date"""
        try:
            query = Transaction.query.filter(
                Transaction.security_id == security.id,
                Transaction.transaction_date <= target_date,
                Transaction.price > 0
            )
            
            if client_id:
                query = query.filter(Transaction.client_id == client_id)
            
            transaction = query.order_by(
                Transaction.transaction_date.desc()
            ).first()
            
            if transaction:
                return {
                    'price': float(transaction.price),
                    'source': 'transaction',
                    'date': transaction.transaction_date,
                    'accuracy': 'exact',
                    'confidence': 1.0,
                    'note': f'Actual transaction price from {transaction.transaction_date}'
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting transaction price: {str(e)}")
            return None
    
    @staticmethod
    def _get_price_from_google_sheets(security, target_date):
        """Get price from Google Sheets HistoricalPrices worksheet"""
        try:
            # Setup Google Sheets
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
            client = gspread.authorize(creds)
            
            # Open spreadsheet
            spreadsheet = client.open_by_key(HistoricalPriceService.SPREADSHEET_KEY)
            
            # Try to get the historical prices worksheet
            try:
                worksheet = spreadsheet.worksheet(HistoricalPriceService.HISTORICAL_SHEET_NAME)
            except gspread.WorksheetNotFound:
                logger.info(f"HistoricalPrices worksheet not found")
                return None
            
            # Get all data
            data = worksheet.get_all_records()
            if not data:
                return None
            
            # Convert to DataFrame for easier searching
            df = pd.DataFrame(data)
            
            # Ensure date column is datetime
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
            target_datetime = pd.to_datetime(target_date)
            
            # Filter by symbol (sheet column may be bare code or ``NSE:CODE``)
            want = _sanitize_symbol(security.symbol or "")
            sym_col = df["Symbol"].astype(str).map(_sanitize_symbol)
            symbol_data = df[sym_col == want]
            
            if symbol_data.empty:
                return None
            
            # Find closest date
            symbol_data['DateDiff'] = (symbol_data['Date'] - target_datetime).abs()
            closest_row = symbol_data.loc[symbol_data['DateDiff'].idxmin()]
            
            # Check if the date is reasonably close (within 7 days)
            if closest_row['DateDiff'].days > 7:
                logger.warning(f"Closest date for {security.symbol} is {closest_row['DateDiff'].days} days away")
            
            price = closest_row.get('Price', None)
            if price and price > 0:
                return {
                    'price': float(price),
                    'source': 'google_sheets',
                    'date': closest_row['Date'].date(),
                    'accuracy': 'exact',
                    'confidence': 0.95,
                    'note': f'Historical price from Google Sheets (date: {closest_row["Date"].date()})'
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting price from Google Sheets: {str(e)}")
            return None
    
    @staticmethod
    def _get_price_from_nifty_proxy(security, target_date):
        """Estimate price using Nifty movement as proxy"""
        try:
            # Get Nifty price at target date
            nifty_at_date = BenchmarkData.query.filter(
                BenchmarkData.date <= target_date
            ).order_by(BenchmarkData.date.desc()).first()
            
            # Get current Nifty price
            nifty_current = BenchmarkData.query.order_by(
                BenchmarkData.date.desc()
            ).first()
            
            if not nifty_at_date or not nifty_current or not security.current_price:
                return None
            
            # Calculate price using Nifty ratio
            nifty_ratio = float(nifty_at_date.price) / float(nifty_current.price)
            estimated_price = float(security.current_price) * nifty_ratio
            
            return {
                'price': estimated_price,
                'source': 'nifty_proxy',
                'date': nifty_at_date.date,
                'accuracy': 'estimated',
                'confidence': 0.75,
                'note': f'Estimated using Nifty movement (ratio: {nifty_ratio:.4f})'
            }
            
        except Exception as e:
            logger.error(f"Error calculating Nifty proxy price: {str(e)}")
            return None
    
    @staticmethod
    def get_price_coverage_report(securities, target_date, client_id=None):
        """
        Get coverage report showing price source for each security
        Useful for debugging and quality assessment
        """
        report = {
            'transaction': [],
            'google_sheets': [],
            'nifty_proxy': [],
            'current_price': [],
            'not_available': []
        }
        
        for security in securities:
            price_data = HistoricalPriceService.get_historical_price(
                security, target_date, client_id
            )
            
            if price_data:
                source = price_data['source']
                report[source].append({
                    'symbol': security.symbol,
                    'price': price_data['price'],
                    'confidence': price_data['confidence']
                })
            else:
                report['not_available'].append(security.symbol)
        
        # Calculate coverage statistics
        total = len(securities)
        stats = {
            'total_securities': total,
            'transaction_coverage': len(report['transaction']),
            'google_sheets_coverage': len(report['google_sheets']),
            'nifty_proxy_coverage': len(report['nifty_proxy']),
            'current_price_coverage': len(report['current_price']),
            'not_available_count': len(report['not_available']),
            'overall_confidence': sum([
                len(report['transaction']) * 1.0,
                len(report['google_sheets']) * 0.95,
                len(report['nifty_proxy']) * 0.75,
                len(report['current_price']) * 0.5
            ]) / total if total > 0 else 0
        }
        
        return {
            'report': report,
            'stats': stats
        }


