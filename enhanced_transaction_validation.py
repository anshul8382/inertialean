"""
Enhanced Transaction Validation with Detailed Error Messages
This module provides improved validation for transaction uploads with specific error descriptions.
"""

import pandas as pd
import re
from datetime import datetime
from typing import List, Dict, Any, Tuple
from models import Security

class EnhancedTransactionValidator:
    """Enhanced validator with detailed error reporting"""
    
    def __init__(self, db_session):
        self.db = db_session
        self.required_columns = ['Date', 'Type', 'Stock', 'Transacted Units', 'Transacted Price (per unit)']
        self.valid_transaction_types = ['BUY', 'SELL', 'SPLIT', 'BONUS']
        self.supported_date_formats = [
            '%d-%b-%Y', '%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%m/%d/%Y',
            '%d-%B-%Y', '%d.%m.%Y', '%d %b %Y', '%d %B %Y'
        ]
    
    def validate_file_structure(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Validate file structure with detailed feedback"""
        errors = []
        warnings = []
        
        # Check if file is empty
        if df.empty:
            return {
                'valid': False,
                'errors': ['The uploaded file is empty. Please ensure it contains transaction data.'],
                'warnings': []
            }
        
        # Check file size
        if len(df) > 1000:
            errors.append(f'File too large. Maximum 1000 transactions allowed. Found {len(df)} transactions.')
        
        # Validate required columns with suggestions
        missing_columns = [col for col in self.required_columns if col not in df.columns]
        if missing_columns:
            available_columns = list(df.columns)
            column_analysis = self._analyze_missing_columns(missing_columns, available_columns)
            errors.append(column_analysis)
        
        # Check for extra columns that might be typos
        extra_columns = [col for col in df.columns if col not in self.required_columns]
        if extra_columns:
            warnings.append(f'Extra columns found: {", ".join(extra_columns)}. These will be ignored.')
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings
        }
    
    def _analyze_missing_columns(self, missing_columns: List[str], available_columns: List[str]) -> str:
        """Analyze missing columns and provide suggestions"""
        suggestions = []
        
        for missing_col in missing_columns:
            # Find similar columns
            similar_cols = self._find_similar_columns(missing_col, available_columns)
            
            if similar_cols:
                suggestions.append(f"'{missing_col}' (did you mean: {', '.join(similar_cols)})")
            else:
                suggestions.append(f"'{missing_col}'")
        
        error_msg = f"Missing required columns: {', '.join(suggestions)}.\n"
        error_msg += f"Available columns in your file: {', '.join(available_columns)}.\n"
        error_msg += f"Required columns must be exactly: {', '.join(self.required_columns)}"
        
        return error_msg
    
    def _find_similar_columns(self, target: str, available: List[str]) -> List[str]:
        """Find similar column names using fuzzy matching"""
        target_lower = target.lower()
        similar = []
        
        for col in available:
            col_lower = col.lower()
            # Check for partial matches
            if target_lower in col_lower or col_lower in target_lower:
                similar.append(col)
            # Check for common variations
            elif self._is_column_variation(target_lower, col_lower):
                similar.append(col)
        
        return similar[:3]  # Limit to 3 suggestions
    
    def _is_column_variation(self, target: str, candidate: str) -> bool:
        """Check if candidate is a variation of target column"""
        variations = {
            'date': ['transaction date', 'txn date', 'trade date'],
            'type': ['transaction type', 'txn type', 'trade type'],
            'stock': ['symbol', 'security', 'company', 'scrip'],
            'transacted units': ['quantity', 'units', 'shares', 'qty'],
            'transacted price (per unit)': ['price', 'rate', 'unit price', 'per unit price']
        }
        
        for key, variants in variations.items():
            if key in target:
                return any(variant in candidate for variant in variants)
        
        return False
    
    def validate_transaction_row(self, row: pd.Series, row_number: int) -> Dict[str, Any]:
        """Validate a single transaction row with detailed error reporting"""
        errors = []
        warnings = []
        data = {}
        
        # Validate date
        date_result = self._validate_date(row, row_number)
        if not date_result['valid']:
            errors.extend(date_result['errors'])
        else:
            data['transaction_date'] = date_result['data']
            if date_result.get('warnings'):
                warnings.extend(date_result['warnings'])
        
        # Validate transaction type
        type_result = self._validate_transaction_type(row, row_number)
        if not type_result['valid']:
            errors.extend(type_result['errors'])
        else:
            data['type'] = type_result['data']
        
        # Validate stock symbol
        stock_result = self._validate_stock_symbol(row, row_number)
        if not stock_result['valid']:
            errors.extend(stock_result['errors'])
        else:
            data['security_id'] = stock_result['data']
            if stock_result.get('warnings'):
                warnings.extend(stock_result['warnings'])
        
        # Validate quantity and price
        qty_price_result = self._validate_quantity_and_price(row, row_number, data.get('type'))
        if not qty_price_result['valid']:
            errors.extend(qty_price_result['errors'])
        else:
            data.update(qty_price_result['data'])
            if qty_price_result.get('warnings'):
                warnings.extend(qty_price_result['warnings'])
        
        return {
            'valid': len(errors) == 0,
            'data': data,
            'errors': errors,
            'warnings': warnings
        }
    
    def _validate_date(self, row: pd.Series, row_number: int) -> Dict[str, Any]:
        """Validate and parse date with detailed error reporting"""
        date_value = row.get('Date')
        
        if pd.isna(date_value) or date_value == '':
            return {
                'valid': False,
                'errors': [f'Row {row_number}: Date is missing or empty. Please provide a valid date.']
            }
        
        # Try different date formats
        parsed_date = None
        used_format = None
        
        for fmt in self.supported_date_formats:
            try:
                if isinstance(date_value, str):
                    parsed_date = datetime.strptime(str(date_value).strip(), fmt)
                    used_format = fmt
                    break
            except ValueError:
                continue
        
        if parsed_date is None:
            # Try pandas parsing as fallback
            try:
                parsed_date = pd.to_datetime(date_value)
                used_format = 'pandas_auto'
            except Exception:
                pass
        
        if parsed_date is None:
            error_msg = f'Row {row_number}: Invalid date format "{date_value}". '
            error_msg += f'Supported formats: DD-MMM-YYYY (e.g., 15-Jan-2024), DD/MM/YYYY, YYYY-MM-DD, DD-MM-YYYY, MM/DD/YYYY'
            return {
                'valid': False,
                'errors': [error_msg]
            }
        
        # Check for special date warnings
        warnings = []
        if parsed_date.date().year == 2000:
            warnings.append(f'Row {row_number}: Date is 1-Jan-2000, which might be a placeholder date.')
        
        return {
            'valid': True,
            'data': parsed_date.date(),
            'warnings': warnings
        }
    
    def _validate_transaction_type(self, row: pd.Series, row_number: int) -> Dict[str, Any]:
        """Validate transaction type with suggestions"""
        type_value = str(row.get('Type', '')).strip().upper()
        
        if not type_value or type_value == 'NAN':
            return {
                'valid': False,
                'errors': [f'Row {row_number}: Transaction type is missing or empty. Valid types: {", ".join(self.valid_transaction_types)}']
            }
        
        if type_value not in self.valid_transaction_types:
            # Find similar transaction types
            similar_types = [t for t in self.valid_transaction_types if type_value in t or t in type_value]
            error_msg = f'Row {row_number}: Invalid transaction type "{type_value}". '
            if similar_types:
                error_msg += f'Did you mean: {", ".join(similar_types)}? '
            error_msg += f'Valid types: {", ".join(self.valid_transaction_types)}'
            return {
                'valid': False,
                'errors': [error_msg]
            }
        
        return {
            'valid': True,
            'data': type_value
        }
    
    def _validate_stock_symbol(self, row: pd.Series, row_number: int) -> Dict[str, Any]:
        """Validate stock symbol with database lookup and suggestions"""
        stock_value = str(row.get('Stock', '')).strip()
        
        if pd.isna(stock_value) or stock_value == '' or stock_value == 'NAN':
            return {
                'valid': False,
                'errors': [f'Row {row_number}: Stock symbol is missing or empty. Please provide a valid stock symbol.']
            }
        
        # Clean the symbol (remove exchange prefixes)
        clean_symbol = re.sub(r'^(NSE:|BSE:|BOM:)\s*', '', stock_value)
        
        # Find security in database
        security = Security.query.filter_by(symbol=clean_symbol).first()
        
        if not security:
            # Try to find similar securities
            similar_securities = self._find_similar_securities(clean_symbol)
            
            error_msg = f'Row {row_number}: Security "{stock_value}" not found in database. '
            if similar_securities:
                suggestions = [f"{s.symbol} ({s.name})" for s in similar_securities]
                error_msg += f'Did you mean: {", ".join(suggestions)}? '
            error_msg += 'Please check the symbol or add the security first.'
            
            return {
                'valid': False,
                'errors': [error_msg]
            }
        
        warnings = []
        if stock_value != clean_symbol:
            warnings.append(f'Row {row_number}: Exchange prefix removed from "{stock_value}" -> "{clean_symbol}"')
        
        return {
            'valid': True,
            'data': security.id,
            'warnings': warnings
        }
    
    def _find_similar_securities(self, symbol: str, limit: int = 5) -> List[Security]:
        """Find similar securities in database"""
        try:
            # Search by symbol similarity
            similar = Security.query.filter(
                Security.symbol.ilike(f'%{symbol}%')
            ).limit(limit).all()
            
            if len(similar) < limit:
                # Also search by name if not enough symbol matches
                name_matches = Security.query.filter(
                    Security.name.ilike(f'%{symbol}%')
                ).limit(limit - len(similar)).all()
                similar.extend(name_matches)
            
            return similar
        except Exception:
            return []
    
    def _validate_quantity_and_price(self, row: pd.Series, row_number: int, transaction_type: str) -> Dict[str, Any]:
        """Validate quantity and price with detailed error reporting"""
        errors = []
        warnings = []
        data = {}
        
        # Validate quantity
        quantity_value = row.get('Transacted Units', '')
        try:
            quantity = float(quantity_value)
            if pd.isna(quantity) or quantity <= 0:
                errors.append(f'Row {row_number}: Quantity must be positive (found: {quantity_value})')
            else:
                data['quantity'] = quantity
        except (ValueError, TypeError):
            errors.append(f'Row {row_number}: Invalid quantity format "{quantity_value}". Expected a positive number.')
        
        # Validate price
        price_value = row.get('Transacted Price (per unit)', '')
        try:
            # Clean price string
            price_str = str(price_value)
            price_str = re.sub(r'[₹,$\s]', '', price_str)
            
            if price_str == '' or price_str.lower() in ['nan', 'none', 'null']:
                price = 0.0
            else:
                price = float(price_str)
            
            if pd.isna(price) or price < 0:
                errors.append(f'Row {row_number}: Price cannot be negative (found: {price_value})')
            elif transaction_type in ['SPLIT', 'BONUS'] and price != 0:
                errors.append(f'Row {row_number}: Price should be 0 for {transaction_type} transactions (found: {price})')
            else:
                data['price'] = price
                
                # Add warning for currency symbols
                if re.search(r'[₹,$]', str(price_value)):
                    warnings.append(f'Row {row_number}: Currency symbols removed from price "{price_value}" -> "{price}"')
                    
        except (ValueError, TypeError):
            error_msg = f'Row {row_number}: Invalid price format "{price_value}". '
            error_msg += 'Expected a number (currency symbols like ₹, $, commas will be removed automatically).'
            errors.append(error_msg)
        
        # Calculate amount
        if 'quantity' in data and 'price' in data:
            if transaction_type in ['SPLIT', 'BONUS']:
                data['amount'] = 0.0
            else:
                data['amount'] = data['quantity'] * data['price']
        
        return {
            'valid': len(errors) == 0,
            'data': data,
            'errors': errors,
            'warnings': warnings
        }
    
    def validate_file(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Validate entire file with detailed error reporting"""
        # First validate file structure
        structure_result = self.validate_file_structure(df)
        if not structure_result['valid']:
            return structure_result
        
        # Validate each row
        all_errors = structure_result['errors']
        all_warnings = structure_result['warnings']
        valid_transactions = []
        
        for index, row in df.iterrows():
            row_result = self.validate_transaction_row(row, index + 2)  # +2 for header and 0-based index
            
            if row_result['valid']:
                valid_transactions.append(row_result['data'])
            else:
                all_errors.extend(row_result['errors'])
            
            all_warnings.extend(row_result['warnings'])
        
        return {
            'valid': len(all_errors) == 0,
            'errors': all_errors,
            'warnings': all_warnings,
            'valid_transactions': valid_transactions,
            'total_rows': len(df),
            'valid_count': len(valid_transactions),
            'error_count': len(all_errors)
        }
