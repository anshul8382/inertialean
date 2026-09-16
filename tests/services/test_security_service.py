"""
Unit Tests for SecurityService

Tests the atomic SecurityService with comprehensive coverage including:
- All methods with valid/invalid inputs
- Edge cases and error handling
- Performance requirements
- Hot stock rating system
- Backward compatibility
"""

import pytest
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime
from models import Security, AssetClass
from services.foundation.security_service import SecurityService
from extensions import db

class TestSecurityService(unittest.TestCase):
    """Test cases for SecurityService"""
    
    def setUp(self):
        """Set up test data"""
        self.app = create_app()
        self.app_context = self.app.app_context()
        self.app_context.push()
        
        # Create test asset class
        self.asset_class = AssetClass(
            id=1,
            name="Equity",
            description="Equity securities",
            created_by=1
        )
        
        # Create test securities
        self.security1 = Security(
            id=1,
            symbol="RELIANCE",
            name="Reliance Industries Ltd",
            asset_class_id=1,
            security_type="EQUITY",
            current_price=2450.50,
            hot_stock_rating=4,
            created_by=1
        )
        
        self.security2 = Security(
            id=2,
            symbol="TCS",
            name="Tata Consultancy Services Ltd",
            asset_class_id=1,
            security_type="EQUITY", 
            current_price=3200.75,
            hot_stock_rating=3,
            created_by=1
        )
        
        self.security3 = Security(
            id=3,
            symbol="INFY",
            name="Infosys Ltd",
            asset_class_id=1,
            security_type="EQUITY",
            current_price=1500.25,
            hot_stock_rating=0,
            created_by=1
        )
    
    def tearDown(self):
        """Clean up after tests"""
        self.app_context.pop()
    
    def test_get_security_valid_id(self):
        """Test getting security with valid ID"""
        with patch('models.Security.query') as mock_query:
            mock_query.get.return_value = self.security1
            
            result = SecurityService.get_security(1)
            
            self.assertIsNotNone(result)
            self.assertEqual(result.symbol, "RELIANCE")
            mock_query.get.assert_called_once_with(1)
    
    def test_get_security_invalid_id(self):
        """Test getting security with invalid ID"""
        with patch('models.Security.query') as mock_query:
            mock_query.get.return_value = None
            
            result = SecurityService.get_security(999)
            
            self.assertIsNone(result)
    
    def test_get_security_negative_id(self):
        """Test getting security with negative ID"""
        result = SecurityService.get_security(-1)
        self.assertIsNone(result)
    
    def test_get_security_zero_id(self):
        """Test getting security with zero ID"""
        result = SecurityService.get_security(0)
        self.assertIsNone(result)
    
    def test_get_security_none_id(self):
        """Test getting security with None ID"""
        result = SecurityService.get_security(None)
        self.assertIsNone(result)
    
    def test_get_security_by_symbol_exact_match(self):
        """Test getting security by exact symbol match"""
        with patch('models.Security.query') as mock_query:
            mock_query.filter_by.return_value.first.return_value = self.security1
            
            result = SecurityService.get_security_by_symbol("RELIANCE")
            
            self.assertIsNotNone(result)
            self.assertEqual(result.symbol, "RELIANCE")
    
    def test_get_security_by_symbol_not_found(self):
        """Test getting security by symbol that doesn't exist"""
        with patch('models.Security.query') as mock_query:
            mock_query.filter_by.return_value.first.return_value = None
            
            result = SecurityService.get_security_by_symbol("NONEXISTENT")
            
            self.assertIsNone(result)
    
    def test_get_security_by_symbol_empty_string(self):
        """Test getting security with empty symbol"""
        result = SecurityService.get_security_by_symbol("")
        self.assertIsNone(result)
        
        result = SecurityService.get_security_by_symbol("   ")
        self.assertIsNone(result)
    
    def test_find_security_by_symbol_exact_match(self):
        """Test finding security with exact symbol match"""
        with patch('models.Security.query') as mock_query:
            mock_query.filter_by.return_value.first.return_value = self.security1
            
            result = SecurityService.find_security_by_symbol("RELIANCE")
            
            self.assertIsNotNone(result)
            self.assertEqual(result.symbol, "RELIANCE")
    
    def test_find_security_by_symbol_case_insensitive(self):
        """Test finding security with case-insensitive match"""
        with patch('models.Security.query') as mock_query:
            # Mock exact match failure
            mock_query.filter_by.return_value.first.return_value = None
            # Mock case-insensitive match success
            mock_query.filter.return_value.first.return_value = self.security1
            
            result = SecurityService.find_security_by_symbol("reliance")
            
            self.assertIsNotNone(result)
            self.assertEqual(result.symbol, "RELIANCE")
    
    def test_find_security_by_symbol_partial_match(self):
        """Test finding security with partial symbol match"""
        with patch('models.Security.query') as mock_query:
            # Mock exact and case-insensitive match failures
            mock_query.filter_by.return_value.first.return_value = None
            mock_query.filter.return_value.first.return_value = None
            # Mock partial match success
            mock_query.filter.return_value.first.return_value = self.security1
            
            result = SecurityService.find_security_by_symbol("RELI")
            
            self.assertIsNotNone(result)
            self.assertEqual(result.symbol, "RELIANCE")
    
    def test_get_securities_by_asset_class_valid(self):
        """Test getting securities by valid asset class"""
        with patch('models.Security.query') as mock_query:
            mock_query.filter_by.return_value.all.return_value = [self.security1, self.security2]
            
            result = SecurityService.get_securities_by_asset_class(1)
            
            self.assertEqual(len(result), 2)
            self.assertIn(self.security1, result)
            self.assertIn(self.security2, result)
    
    def test_get_securities_by_asset_class_invalid(self):
        """Test getting securities by invalid asset class"""
        result = SecurityService.get_securities_by_asset_class(-1)
        self.assertEqual(result, [])
        
        result = SecurityService.get_securities_by_asset_class(0)
        self.assertEqual(result, [])
    
    def test_get_hot_stocks_min_rating(self):
        """Test getting hot stocks with minimum rating"""
        with patch('models.Security.query') as mock_query:
            mock_query.filter.return_value.order_by.return_value.all.return_value = [self.security1, self.security2]
            
            result = SecurityService.get_hot_stocks(min_rating=3)
            
            self.assertEqual(len(result), 2)
            self.assertIn(self.security1, result)
            self.assertIn(self.security2, result)
    
    def test_get_hot_stocks_invalid_rating(self):
        """Test getting hot stocks with invalid rating"""
        result = SecurityService.get_hot_stocks(min_rating=-1)
        self.assertEqual(result, [])
        
        result = SecurityService.get_hot_stocks(min_rating=6)
        self.assertEqual(result, [])
    
    def test_get_top_hot_stocks(self):
        """Test getting top hot stocks"""
        with patch('models.Security.query') as mock_query:
            mock_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [self.security1]
            
            result = SecurityService.get_top_hot_stocks(limit=5)
            
            self.assertEqual(len(result), 1)
            self.assertIn(self.security1, result)
    
    def test_get_securities_by_rating(self):
        """Test getting securities by specific rating"""
        with patch('models.Security.query') as mock_query:
            mock_query.filter_by.return_value.all.return_value = [self.security1]
            
            result = SecurityService.get_securities_by_rating(4)
            
            self.assertEqual(len(result), 1)
            self.assertIn(self.security1, result)
    
    def test_get_securities_by_rating_invalid(self):
        """Test getting securities by invalid rating"""
        result = SecurityService.get_securities_by_rating(-1)
        self.assertEqual(result, [])
        
        result = SecurityService.get_securities_by_rating(6)
        self.assertEqual(result, [])
    
    def test_update_hot_stock_rating_valid(self):
        """Test updating hot stock rating with valid values"""
        with patch('models.Security.query') as mock_query:
            mock_security = MagicMock()
            mock_security.hot_stock_rating = 3
            mock_query.get.return_value = mock_security
            
            with patch('extensions.db.session') as mock_session:
                result = SecurityService.update_hot_stock_rating(1, 4)
                
                self.assertTrue(result)
                self.assertEqual(mock_security.hot_stock_rating, 4)
                mock_session.commit.assert_called_once()
    
    def test_update_hot_stock_rating_invalid_rating(self):
        """Test updating hot stock rating with invalid rating"""
        result = SecurityService.update_hot_stock_rating(1, -1)
        self.assertFalse(result)
        
        result = SecurityService.update_hot_stock_rating(1, 6)
        self.assertFalse(result)
    
    def test_update_hot_stock_rating_security_not_found(self):
        """Test updating hot stock rating for non-existent security"""
        with patch('models.Security.query') as mock_query:
            mock_query.get.return_value = None
            
            result = SecurityService.update_hot_stock_rating(999, 4)
            
            self.assertFalse(result)
    
    def test_validate_security_exists_valid(self):
        """Test validating existing security"""
        with patch('models.Security.query') as mock_query:
            mock_query.filter_by.return_value.first.return_value = self.security1
            
            result = SecurityService.validate_security_exists(1)
            
            self.assertTrue(result)
    
    def test_validate_security_exists_invalid(self):
        """Test validating non-existent security"""
        with patch('models.Security.query') as mock_query:
            mock_query.filter_by.return_value.first.return_value = None
            
            result = SecurityService.validate_security_exists(999)
            
            self.assertFalse(result)
    
    def test_validate_security_exists_invalid_id(self):
        """Test validating security with invalid ID"""
        result = SecurityService.validate_security_exists(-1)
        self.assertFalse(result)
        
        result = SecurityService.validate_security_exists(0)
        self.assertFalse(result)
    
    def test_get_security_info_valid(self):
        """Test getting complete security info"""
        with patch.object(SecurityService, 'get_security') as mock_get:
            mock_get.return_value = self.security1
            
            result = SecurityService.get_security_info(1)
            
            self.assertIsNotNone(result)
            self.assertEqual(result['id'], 1)
            self.assertEqual(result['symbol'], "RELIANCE")
            self.assertEqual(result['hot_stock_rating'], 4)
            self.assertEqual(result['hot_stock_label'], "High Priority")
    
    def test_get_security_info_not_found(self):
        """Test getting security info for non-existent security"""
        with patch.object(SecurityService, 'get_security') as mock_get:
            mock_get.return_value = None
            
            result = SecurityService.get_security_info(999)
            
            self.assertIsNone(result)
    
    def test_search_securities_by_symbol(self):
        """Test searching securities by symbol"""
        with patch('models.Security.query') as mock_query:
            mock_query.filter.return_value.limit.return_value.all.return_value = [self.security1]
            
            result = SecurityService.search_securities("RELI", limit=5)
            
            self.assertEqual(len(result), 1)
            self.assertIn(self.security1, result)
    
    def test_search_securities_by_name(self):
        """Test searching securities by name"""
        with patch('models.Security.query') as mock_query:
            mock_query.filter.return_value.limit.return_value.all.return_value = [self.security1]
            
            result = SecurityService.search_securities("Reliance", limit=5)
            
            self.assertEqual(len(result), 1)
            self.assertIn(self.security1, result)
    
    def test_search_securities_empty_query(self):
        """Test searching securities with empty query"""
        result = SecurityService.search_securities("")
        self.assertEqual(result, [])
        
        result = SecurityService.search_securities("   ")
        self.assertEqual(result, [])
    
    def test_get_hot_stock_analytics(self):
        """Test getting hot stock analytics"""
        with patch('models.Security.query') as mock_query:
            # Mock total count
            mock_query.count.return_value = 100
            # Mock hot stocks count
            mock_query.filter.return_value.count.return_value = 20
            # Mock rating distribution
            mock_query.filter_by.return_value.count.return_value = 5
            
            with patch.object(SecurityService, 'get_top_hot_stocks') as mock_top:
                mock_top.return_value = [self.security1]
                
                result = SecurityService.get_hot_stock_analytics()
                
                self.assertIsNotNone(result)
                self.assertEqual(result['total_securities'], 100)
                self.assertEqual(result['hot_securities'], 20)
                self.assertIn('rating_distribution', result)
                self.assertIn('top_rated_securities', result)
                self.assertIn('rating_definitions', result)
    
    def test_is_hot_stock_backward_compatibility(self):
        """Test backward compatibility for hot stock check"""
        with patch.object(SecurityService, 'get_security') as mock_get:
            # Test hot stock (rating >= 3)
            mock_security = MagicMock()
            mock_security.hot_stock_rating = 4
            mock_get.return_value = mock_security
            
            result = SecurityService.is_hot_stock(1)
            self.assertTrue(result)
            
            # Test non-hot stock (rating < 3)
            mock_security.hot_stock_rating = 2
            result = SecurityService.is_hot_stock(1)
            self.assertFalse(result)
            
            # Test not found
            mock_get.return_value = None
            result = SecurityService.is_hot_stock(999)
            self.assertFalse(result)
    
    def test_hot_stock_rating_definitions(self):
        """Test hot stock rating definitions"""
        ratings = SecurityService.HOT_STOCK_RATINGS
        
        self.assertEqual(ratings[0], "Not Hot")
        self.assertEqual(ratings[1], "Watch List")
        self.assertEqual(ratings[2], "Research Interest")
        self.assertEqual(ratings[3], "Recommended")
        self.assertEqual(ratings[4], "High Priority")
        self.assertEqual(ratings[5], "Must Have")
    
    def test_error_handling_database_error(self):
        """Test error handling for database errors"""
        with patch('models.Security.query') as mock_query:
            mock_query.get.side_effect = Exception("Database error")
            
            result = SecurityService.get_security(1)
            
            self.assertIsNone(result)
    
    def test_performance_requirements(self):
        """Test that methods meet performance requirements (< 10ms)"""
        import time
        
        with patch('models.Security.query') as mock_query:
            mock_query.get.return_value = self.security1
            
            start_time = time.time()
            SecurityService.get_security(1)
            end_time = time.time()
            
            execution_time = (end_time - start_time) * 1000  # Convert to milliseconds
            self.assertLess(execution_time, 10, f"Method took {execution_time}ms, should be < 10ms")

def create_app():
    """Create test app"""
    from flask import Flask
    from extensions import db
    
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    db.init_app(app)
    
    return app

if __name__ == '__main__':
    # Run tests with verbose output
    unittest.main(verbosity=2)


