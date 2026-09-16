"""
SecurityService - Atomic Foundation Service

Provides secure, reliable access to security data with NO dependencies.
This is the foundation service that other services will depend on.

Features:
- Hot stock rating system (0-5 scale)
- Fuzzy symbol matching
- Comprehensive error handling
- Performance optimized queries
- Backward compatibility support
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from models import Security, AssetClass
from extensions import db
import logging

logger = logging.getLogger(__name__)

def _safe_db_query(query_func):
    """Helper to safely execute database queries with or without app context"""
    try:
        from flask import current_app
        with current_app.app_context():
            return query_func()
    except RuntimeError:
        # No app context available, execute directly
        return query_func()

class SecurityService:
    """
    Atomic service for security operations - NO dependencies
    
    Hot Stock Rating Scale:
    0 = Not Hot (default)
    1 = Watch List (monitor for potential)
    2 = Research Interest (under research consideration)
    3 = Recommended (research team recommends)
    4 = High Priority (strong recommendation)
    5 = Must Have (critical for portfolios)
    """
    
    # Hot stock rating definitions
    HOT_STOCK_RATINGS = {
        0: "Not Hot",
        1: "Watch List", 
        2: "Research Interest",
        3: "Recommended",
        4: "High Priority",
        5: "Must Have"
    }
    
    @staticmethod
    def get_security(security_id: int) -> Optional[Security]:
        """
        Get security by ID with error handling
        
        Args:
            security_id: Security ID to lookup
            
        Returns:
            Security object or None if not found
            
        Example:
            security = SecurityService.get_security(123)
            if security:
                print(f"Found: {security.symbol}")
        """
        try:
            if not security_id or security_id <= 0:
                logger.warning(f"Invalid security_id: {security_id}")
                return None
                
            security = _safe_db_query(lambda: Security.query.get(security_id))
            if not security:
                logger.info(f"Security not found: {security_id}")
            return security
            
        except Exception as e:
            logger.error(f"Error getting security {security_id}: {str(e)}")
            return None
    
    @staticmethod
    def get_security_by_symbol(symbol: str) -> Optional[Security]:
        """
        Get security by exact symbol match
        
        Args:
            symbol: Exact security symbol
            
        Returns:
            Security object or None if not found
            
        Example:
            security = SecurityService.get_security_by_symbol("RELIANCE")
        """
        try:
            if not symbol or not symbol.strip():
                logger.warning("Empty symbol provided")
                return None
                
            symbol = symbol.strip().upper()
            
            security = _safe_db_query(lambda: Security.query.filter_by(symbol=symbol).first())
            if not security:
                logger.info(f"Security not found for symbol: {symbol}")
            return security
            
        except Exception as e:
            logger.error(f"Error getting security by symbol '{symbol}': {str(e)}")
            return None
    
    @staticmethod
    def find_security_by_symbol(symbol: str) -> Optional[Security]:
        """
        Find security with fuzzy matching (case-insensitive, partial)
        
        Args:
            symbol: Security symbol (can be partial)
            
        Returns:
            Security object or None if not found
            
        Example:
            security = SecurityService.find_security_by_symbol("rel")  # Finds RELIANCE
        """
        try:
            if not symbol or not symbol.strip():
                logger.warning("Empty symbol provided")
                return None
                
            symbol = symbol.strip()
            
            # Try exact match first
            security = Security.query.filter_by(symbol=symbol.upper()).first()
            if security:
                return security
            
            # Try case-insensitive exact match
            security = Security.query.filter(Security.symbol.ilike(symbol.upper())).first()
            if security:
                return security
            
            # Try partial match
            security = Security.query.filter(Security.symbol.ilike(f"%{symbol.upper()}%")).first()
            if security:
                return security
            
            logger.info(f"No security found for symbol: {symbol}")
            return None
            
        except Exception as e:
            logger.error(f"Error finding security by symbol '{symbol}': {str(e)}")
            return None
    
    @staticmethod
    def get_securities_by_asset_class(asset_class_id: int) -> List[Security]:
        """
        Get all securities in an asset class
        
        Args:
            asset_class_id: Asset class ID
            
        Returns:
            List of Security objects
            
        Example:
            equity_securities = SecurityService.get_securities_by_asset_class(1)
        """
        try:
            if not asset_class_id or asset_class_id <= 0:
                logger.warning(f"Invalid asset_class_id: {asset_class_id}")
                return []
                
            securities = Security.query.filter_by(asset_class_id=asset_class_id).all()
            logger.info(f"Found {len(securities)} securities for asset class {asset_class_id}")
            return securities
            
        except Exception as e:
            logger.error(f"Error getting securities for asset class {asset_class_id}: {str(e)}")
            return []
    
    @staticmethod
    def get_hot_stocks(min_rating: int = 1) -> List[Security]:
        """
        Get securities with hot stock rating >= min_rating
        
        Args:
            min_rating: Minimum hot stock rating (1-5)
            
        Returns:
            List of hot Security objects
            
        Example:
            hot_stocks = SecurityService.get_hot_stocks(min_rating=3)
        """
        try:
            if min_rating < 0 or min_rating > 5:
                logger.warning(f"Invalid min_rating: {min_rating}, using default 1")
                min_rating = 1
                
            securities = Security.query.filter(
                Security.hot_stock_rating >= min_rating
            ).order_by(Security.hot_stock_rating.desc()).all()
            
            logger.info(f"Found {len(securities)} hot stocks with rating >= {min_rating}")
            return securities
            
        except Exception as e:
            logger.error(f"Error getting hot stocks: {str(e)}")
            return []
    
    @staticmethod
    def get_top_hot_stocks(limit: int = 10) -> List[Security]:
        """
        Get top rated hot stocks (rating 4-5)
        
        Args:
            limit: Maximum number of stocks to return
            
        Returns:
            List of top hot Security objects
            
        Example:
            top_stocks = SecurityService.get_top_hot_stocks(limit=5)
        """
        try:
            if limit <= 0:
                limit = 10
                
            securities = Security.query.filter(
                Security.hot_stock_rating >= 4
            ).order_by(Security.hot_stock_rating.desc()).limit(limit).all()
            
            logger.info(f"Found {len(securities)} top hot stocks")
            return securities
            
        except Exception as e:
            logger.error(f"Error getting top hot stocks: {str(e)}")
            return []
    
    @staticmethod
    def get_securities_by_rating(rating: int) -> List[Security]:
        """
        Get all securities with specific hot stock rating
        
        Args:
            rating: Hot stock rating (0-5)
            
        Returns:
            List of Security objects with that rating
            
        Example:
            recommended = SecurityService.get_securities_by_rating(3)
        """
        try:
            if rating < 0 or rating > 5:
                logger.warning(f"Invalid rating: {rating}")
                return []
                
            securities = Security.query.filter_by(hot_stock_rating=rating).all()
            logger.info(f"Found {len(securities)} securities with rating {rating}")
            return securities
            
        except Exception as e:
            logger.error(f"Error getting securities by rating {rating}: {str(e)}")
            return []
    
    @staticmethod
    def update_hot_stock_rating(security_id: int, rating: int) -> bool:
        """
        Update hot stock rating (0-5 scale)
        
        Args:
            security_id: Security ID to update
            rating: New rating (0-5)
            
        Returns:
            True if successful, False otherwise
            
        Example:
            success = SecurityService.update_hot_stock_rating(123, 4)
        """
        try:
            if rating < 0 or rating > 5:
                logger.warning(f"Invalid rating {rating} for security {security_id}")
                return False
                
            security = Security.query.get(security_id)
            if not security:
                logger.warning(f"Security not found: {security_id}")
                return False
                
            old_rating = security.hot_stock_rating
            security.hot_stock_rating = rating
            security.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            logger.info(f"Updated security {security_id} rating: {old_rating} -> {rating}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating hot stock rating for security {security_id}: {str(e)}")
            db.session.rollback()
            return False
    
    @staticmethod
    def validate_security_exists(security_id: int) -> bool:
        """
        Validate if security exists (for transaction validation)
        
        Args:
            security_id: Security ID to validate
            
        Returns:
            True if exists, False otherwise
            
        Example:
            if SecurityService.validate_security_exists(123):
                # Proceed with transaction
        """
        try:
            if not security_id or security_id <= 0:
                return False
                
            exists = Security.query.filter_by(id=security_id).first() is not None
            return exists
            
        except Exception as e:
            logger.error(f"Error validating security {security_id}: {str(e)}")
            return False
    
    @staticmethod
    def get_security_info(security_id: int) -> Optional[Dict[str, Any]]:
        """
        Get complete security information as dictionary
        
        Args:
            security_id: Security ID to lookup
            
        Returns:
            Dictionary with all security info or None
            
        Example:
            info = SecurityService.get_security_info(123)
            if info:
                print(f"Symbol: {info['symbol']}, Rating: {info['hot_stock_rating']}")
        """
        try:
            security = SecurityService.get_security(security_id)
            if not security:
                return None
                
            return {
                'id': security.id,
                'symbol': security.symbol,
                'name': security.name,
                'asset_class_id': security.asset_class_id,
                'security_type': security.security_type,
                'current_price': float(security.current_price) if security.current_price else None,
                'last_updated': security.last_updated.isoformat() if security.last_updated else None,
                'hot_stock_rating': security.hot_stock_rating,
                'hot_stock_label': SecurityService.HOT_STOCK_RATINGS.get(security.hot_stock_rating, "Unknown"),
                'meta_data': security.meta_data,
                'created_at': security.created_at.isoformat() if security.created_at else None,
                'updated_at': security.updated_at.isoformat() if security.updated_at else None
            }
            
        except Exception as e:
            logger.error(f"Error getting security info for {security_id}: {str(e)}")
            return None
    
    @staticmethod
    def search_securities(query: str, limit: int = 10) -> List[Security]:
        """
        Search securities by symbol or name
        
        Args:
            query: Search query
            limit: Maximum results to return
            
        Returns:
            List of matching Security objects
            
        Example:
            results = SecurityService.search_securities("RELI", limit=5)
        """
        try:
            if not query or not query.strip():
                return []
                
            query = query.strip()
            if limit <= 0:
                limit = 10
                
            # Search by symbol first, then by name
            securities = Security.query.filter(
                (Security.symbol.ilike(f"%{query.upper()}%")) |
                (Security.name.ilike(f"%{query}%"))
            ).limit(limit).all()
            
            logger.info(f"Found {len(securities)} securities for query: {query}")
            return securities
            
        except Exception as e:
            logger.error(f"Error searching securities for '{query}': {str(e)}")
            return []
    
    @staticmethod
    def get_hot_stock_analytics() -> Dict[str, Any]:
        """
        Get comprehensive hot stock analytics
        
        Returns:
            Dictionary with hot stock analytics
            
        Example:
            analytics = SecurityService.get_hot_stock_analytics()
            print(f"Total hot stocks: {analytics['hot_securities']}")
        """
        try:
            total_securities = Security.query.count()
            hot_securities = Security.query.filter(Security.hot_stock_rating > 0).count()
            
            rating_distribution = {}
            for rating in range(6):  # 0-5
                count = Security.query.filter_by(hot_stock_rating=rating).count()
                rating_distribution[f'rating_{rating}'] = count
            
            top_rated = SecurityService.get_top_hot_stocks(limit=10)
            
            return {
                'total_securities': total_securities,
                'hot_securities': hot_securities,
                'rating_distribution': rating_distribution,
                'top_rated_securities': [
                    {
                        'id': sec.id,
                        'symbol': sec.symbol,
                        'name': sec.name,
                        'rating': sec.hot_stock_rating,
                        'label': SecurityService.HOT_STOCK_RATINGS.get(sec.hot_stock_rating, "Unknown")
                    } for sec in top_rated
                ],
                'rating_definitions': SecurityService.HOT_STOCK_RATINGS
            }
            
        except Exception as e:
            logger.error(f"Error getting hot stock analytics: {str(e)}")
            return {}
    
    @staticmethod
    def is_hot_stock(security_id: int) -> bool:
        """
        Backward compatibility - returns True if rating >= 3
        
        Args:
            security_id: Security ID to check
            
        Returns:
            True if hot stock (rating >= 3), False otherwise
            
        Example:
            if SecurityService.is_hot_stock(123):
                print("This is a hot stock!")
        """
        try:
            security = SecurityService.get_security(security_id)
            if not security:
                return False
                
            return security.hot_stock_rating >= 3
            
        except Exception as e:
            logger.error(f"Error checking hot stock status for {security_id}: {str(e)}")
            return False

