#!/usr/bin/env python3
"""
Historical Price API
Comprehensive historical price service with waterfall fallback strategy
"""

import logging
from datetime import datetime, date, timedelta
from flask import Blueprint
from sqlalchemy import and_

from api.core.response import APIResponse
from api.core.exceptions import NotFoundError, ValidationError
from models import db, Security, Transaction, HistoricalPrice
from services.google_sheets_historical_price import GoogleSheetsHistoricalPrice

logger = logging.getLogger(__name__)

historical_prices_bp = Blueprint('historical_prices', __name__)

class HistoricalPriceService:
    """
    Comprehensive Historical Price Service
    
    Waterfall Strategy:
    1. Check HistoricalPrice table (database cache)
    2. Check Transaction table (actual trade prices)
    3. Fetch from Google Sheets GOOGLEFINANCE (real market data)
    4. Save to database for future use
    """
    
    @staticmethod
    def get_historical_price(security_id, target_date, use_cache=True, save_to_db=True):
        """
        Get historical price for a security on a specific date
        
        Args:
            security_id: Security ID
            target_date: date object or string 'YYYY-MM-DD'
            use_cache: Whether to check database cache first
            save_to_db: Whether to save fetched price to database
            
        Returns:
            dict with price, source, confidence, date
        """
        try:
            # Get security
            security = Security.query.get(security_id)
            if not security:
                logger.error(f"Security {security_id} not found")
                return None
            
            # Normalize date
            if isinstance(target_date, str):
                target_date = datetime.strptime(target_date, '%Y-%m-%d').date()
            
            logger.info(f"=== HISTORICAL PRICE REQUEST ===")
            logger.info(f"Security: {security.symbol} (ID: {security_id})")
            logger.info(f"Date: {target_date}")
            
            # PRIORITY 1: Check HistoricalPrice table (database cache)
            if use_cache:
                logger.info("Step 1: Checking HistoricalPrice table...")
                cached_price = HistoricalPrice.query.filter_by(
                    security_id=security_id,
                    date=target_date
                ).first()
                
                if cached_price:
                    logger.info(f"✅ Found in cache: ₹{cached_price.close_price} (source: {cached_price.source})")
                    return {
                        'price': float(cached_price.close_price),
                        'source': f'cache_{cached_price.source}',
                        'original_source': cached_price.source,
                        'date': cached_price.date,
                        'accuracy': 'exact',
                        'confidence': float(cached_price.confidence),
                        'note': f'From database cache (original source: {cached_price.source})'
                    }
                else:
                    logger.info("Not found in cache, proceeding to fallbacks...")
            
            # PRIORITY 2: Check Transaction table
            logger.info("Step 2: Checking Transaction table...")
            transaction_price = HistoricalPriceService._get_from_transaction(
                security_id, target_date
            )
            
            if transaction_price:
                logger.info(f"✅ Found in transactions: ₹{transaction_price['price']}")
                
                # Save to database
                if save_to_db:
                    HistoricalPriceService._save_to_database(
                        security_id, target_date, transaction_price['price'],
                        source='transaction', confidence=1.0
                    )
                
                return transaction_price
            else:
                logger.info("No transaction found, trying Google Sheets...")
            
            # PRIORITY 3: Fetch from Google Sheets GOOGLEFINANCE
            logger.info("Step 3: Fetching from Google Sheets GOOGLEFINANCE...")
            googlefinance_price = GoogleSheetsHistoricalPrice.get_historical_price(
                security.symbol, target_date
            )
            
            if googlefinance_price:
                logger.info(f"✅ Got from GOOGLEFINANCE: ₹{googlefinance_price['price']}")
                
                # Save to database for future use
                if save_to_db:
                    HistoricalPriceService._save_to_database(
                        security_id, target_date, googlefinance_price['price'],
                        source='googlefinance', confidence=0.98
                    )
                
                return googlefinance_price
            else:
                logger.warning(f"Google Sheets GOOGLEFINANCE failed for {security.symbol}")
            
            # PRIORITY 4: Fallback to current price (last resort)
            logger.warning(f"All methods failed, using current price as fallback")
            if security.current_price:
                return {
                    'price': float(security.current_price),
                    'source': 'current_price_fallback',
                    'date': datetime.now().date(),
                    'accuracy': 'approximate',
                    'confidence': 0.5,
                    'note': f'Using current price (no historical data available for {target_date})'
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting historical price: {str(e)}")
            return None
    
    @staticmethod
    def _get_from_transaction(security_id, target_date):
        """Get price from transaction closest to or before target date"""
        try:
            # Find transaction on or before target date
            transaction = Transaction.query.filter(
                Transaction.security_id == security_id,
                Transaction.transaction_date <= target_date,
                Transaction.price > 0
            ).order_by(Transaction.transaction_date.desc()).first()
            
            if transaction:
                days_diff = (target_date - transaction.transaction_date.date()).days
                
                return {
                    'price': float(transaction.price),
                    'source': 'transaction',
                    'date': transaction.transaction_date.date(),
                    'accuracy': 'exact',
                    'confidence': 1.0,
                    'note': f'Actual transaction price (transaction date: {transaction.transaction_date.date()}, {days_diff} days before target)'
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting transaction price: {str(e)}")
            return None
    
    @staticmethod
    def _save_to_database(security_id, price_date, price, source='googlefinance', confidence=0.98):
        """Save historical price to database"""
        try:
            # Check if already exists
            existing = HistoricalPrice.query.filter_by(
                security_id=security_id,
                date=price_date
            ).first()
            
            if existing:
                # Update if new source has higher confidence
                if confidence > float(existing.confidence):
                    existing.close_price = price
                    existing.source = source
                    existing.confidence = confidence
                    existing.updated_at = datetime.utcnow()
                    db.session.commit()
                    logger.info(f"Updated existing historical price with better source")
            else:
                # Create new record
                historical_price = HistoricalPrice(
                    security_id=security_id,
                    date=price_date,
                    close_price=price,
                    source=source,
                    confidence=confidence
                )
                db.session.add(historical_price)
                db.session.commit()
                logger.info(f"Saved new historical price to database")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error saving historical price: {str(e)}")
    
    @staticmethod
    def get_historical_prices_batch(security_date_pairs, use_cache=True, save_to_db=True):
        """
        Get historical prices for multiple security-date pairs efficiently
        
        Args:
            security_date_pairs: List of tuples [(security_id, date), ...]
            use_cache: Whether to use cached data
            save_to_db: Whether to save fetched prices
            
        Returns:
            dict: {(security_id, date): price_data, ...}
        """
        results = {}
        missing_pairs = []
        
        # Step 1: Check cache for all pairs
        if use_cache:
            for security_id, target_date in security_date_pairs:
                cached = HistoricalPrice.query.filter_by(
                    security_id=security_id,
                    date=target_date
                ).first()
                
                if cached:
                    results[(security_id, target_date)] = {
                        'price': float(cached.close_price),
                        'source': f'cache_{cached.source}',
                        'confidence': float(cached.confidence)
                    }
                else:
                    missing_pairs.append((security_id, target_date))
        else:
            missing_pairs = security_date_pairs
        
        logger.info(f"Batch request: {len(security_date_pairs)} pairs, {len(missing_pairs)} not in cache")
        
        # Step 2: Get missing prices
        for security_id, target_date in missing_pairs:
            price_data = HistoricalPriceService.get_historical_price(
                security_id, target_date, use_cache=False, save_to_db=save_to_db
            )
            if price_data:
                results[(security_id, target_date)] = price_data
        
        return results


# ============================================================================
# API ENDPOINTS
# ============================================================================

@historical_prices_bp.route('/<int:security_id>/price/<string:date>', methods=['GET'])
def get_price_for_date(security_id, date):
    """
    Get historical price for a security on a specific date
    
    Example: GET /api/v1/securities/123/price/2025-04-09
    """
    try:
        price_data = HistoricalPriceService.get_historical_price(security_id, date)
        
        if not price_data:
            return APIResponse.error(
                message=f"Could not find price for security {security_id} on {date}",
                status_code=404
            )
        
        return APIResponse.success(
            data=price_data,
            message=f"Historical price retrieved (source: {price_data['source']})"
        )
        
    except Exception as e:
        logger.error(f"Error in get_price_for_date: {str(e)}")
        return APIResponse.error(
            message=str(e),
            status_code=500
        )


@historical_prices_bp.route('/batch', methods=['POST'])
def get_prices_batch():
    """
    Get historical prices for multiple securities and dates in batch
    
    POST body:
    {
        "requests": [
            {"security_id": 123, "date": "2025-04-09"},
            {"security_id": 456, "date": "2025-05-15"}
        ]
    }
    """
    try:
        from flask import request
        data = request.get_json()
        
        if not data or 'requests' not in data:
            raise ValidationError("Missing 'requests' in request body")
        
        requests_list = data['requests']
        pairs = [(r['security_id'], r['date']) for r in requests_list]
        
        results = HistoricalPriceService.get_historical_prices_batch(pairs)
        
        # Format response
        formatted_results = []
        for (security_id, date), price_data in results.items():
            formatted_results.append({
                'security_id': security_id,
                'date': str(date),
                **price_data
            })
        
        return APIResponse.success(
            data=formatted_results,
            message=f"Retrieved {len(formatted_results)} historical prices"
        )
        
    except Exception as e:
        logger.error(f"Error in batch price fetch: {str(e)}")
        return APIResponse.error(
            message=str(e),
            status_code=500
        )


@historical_prices_bp.route('/coverage/<int:security_id>', methods=['GET'])
def get_price_coverage(security_id):
    """
    Get coverage report for a security showing available historical data
    
    Example: GET /api/v1/securities/123/coverage
    """
    try:
        security = Security.query.get_or_404(security_id)
        
        # Get all historical prices for this security
        historical_prices = HistoricalPrice.query.filter_by(
            security_id=security_id
        ).order_by(HistoricalPrice.date).all()
        
        # Get all transactions for this security
        transactions = Transaction.query.filter_by(
            security_id=security_id
        ).order_by(Transaction.transaction_date).all()
        
        coverage_report = {
            'security': {
                'id': security.id,
                'symbol': security.symbol,
                'name': security.name
            },
            'historical_prices_count': len(historical_prices),
            'transactions_count': len(transactions),
            'coverage': {
                'earliest_date': historical_prices[0].date if historical_prices else None,
                'latest_date': historical_prices[-1].date if historical_prices else None,
                'total_days': len(historical_prices)
            },
            'sources': {
                'cron': sum(1 for hp in historical_prices if hp.source == 'cron'),
                'transaction': sum(1 for hp in historical_prices if hp.source == 'transaction'),
                'googlefinance': sum(1 for hp in historical_prices if hp.source == 'googlefinance'),
                'manual': sum(1 for hp in historical_prices if hp.source == 'manual')
            }
        }
        
        return APIResponse.success(
            data=coverage_report,
            message=f"Price coverage for {security.symbol}"
        )
        
    except Exception as e:
        logger.error(f"Error getting price coverage: {str(e)}")
        return APIResponse.error(
            message=str(e),
            status_code=500
        )


