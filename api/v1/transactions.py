"""
Transaction API v1
Comprehensive API for transaction management with holdings and cashflow integration
"""
from flask import Blueprint, request, current_app
from flask_login import current_user, login_required
from api.core.response import APIResponse
# from api.core.decorators import api_auth_required, api_rate_limit
from api.core.exceptions import ValidationError, NotFoundError, ConflictError
from models import (
    Client, Transaction, Holding, Cashflow, Security, Portfolio, MProfitSymbolMap
)
from extensions import db
from datetime import datetime, date, time
import logging
import re
import json

from sqlalchemy import or_, func

from services.mprofit_trade_import_service import MProfitTradeImportService

logger = logging.getLogger(__name__)

# Create blueprint
transactions_bp = Blueprint('transactions_api', __name__)

# ============================================================================
# TRANSACTION CRUD OPERATIONS
# ============================================================================

@transactions_bp.route('/<int:client_id>/transactions', methods=['GET'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=60)
def get_client_transactions(client_id):
    """
    Get all transactions for a client with pagination and filtering
    
    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50, max: 100)
        - transaction_type: Filter by transaction type (BUY, SELL, DIVIDEND, etc.)
        - security_id: Filter by security
        - date_from: Filter from date (YYYY-MM-DD)
        - date_to: Filter to date (YYYY-MM-DD)
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 100)
        transaction_type = request.args.get('transaction_type', '').strip()
        security_id = request.args.get('security_id', type=int)
        date_from = request.args.get('date_from', '').strip()
        date_to = request.args.get('date_to', '').strip()
        
        # Build query
        query = Transaction.query.filter_by(client_id=client_id)
        
        # Apply filters
        if transaction_type:
            query = query.filter(Transaction.type == transaction_type)
        
        if security_id:
            query = query.filter(Transaction.security_id == security_id)
        
        if date_from:
            try:
                from_date = datetime.strptime(date_from, '%Y-%m-%d').date()
                query = query.filter(Transaction.transaction_date >= from_date)
            except ValueError:
                raise ValidationError("date_from must be in YYYY-MM-DD format")
        
        if date_to:
            try:
                to_date = datetime.strptime(date_to, '%Y-%m-%d').date()
                query = query.filter(Transaction.transaction_date <= to_date)
            except ValueError:
                raise ValidationError("date_to must be in YYYY-MM-DD format")
        
        # Get total count
        total = query.count()
        
        # Apply pagination and ordering
        transactions = query.order_by(Transaction.transaction_date.desc()).offset(
            (page - 1) * per_page
        ).limit(per_page).all()
        
        # Format response data
        transactions_data = []
        for transaction in transactions:
            transaction_data = {
                'id': transaction.id,
                'client_id': transaction.client_id,
                'security_id': transaction.security_id,
                'security_name': transaction.security.name if transaction.security else None,
                'type': transaction.type,
                'quantity': float(transaction.quantity) if transaction.quantity else 0,
                'price': float(transaction.price) if transaction.price else 0,
                'amount': float(transaction.amount) if transaction.amount else 0,
                'transaction_date': transaction.transaction_date.isoformat(),
                'created_at': transaction.created_at.isoformat() if transaction.created_at else None,
                'notes': getattr(transaction, 'notes', None)
            }
            transactions_data.append(transaction_data)
        
        return APIResponse.paginated(
            data=transactions_data,
            page=page,
            per_page=per_page,
            total=total,
            message=f"Retrieved {len(transactions_data)} transactions"
        )
        
    except Exception as e:
        logger.error(f"Error getting transactions for client {client_id}: {str(e)}")
        raise

@transactions_bp.route('/<int:client_id>/transactions', methods=['POST'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=10)
def create_transaction(client_id):
    """
    Create a new transaction and update holdings and cashflows
    
    Required fields:
        - security_id: Security ID
        - type: Transaction type (BUY, SELL, DIVIDEND, SPLIT, BONUS)
        - quantity: Quantity of securities
        - price: Price per security
        - transaction_date: Transaction date (YYYY-MM-DD)
    
    Optional fields:
        - amount: Total amount (calculated if not provided)
        - fees: Transaction fees
        - notes: Transaction notes
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['security_id', 'type', 'quantity', 'price', 'transaction_date']
        for field in required_fields:
            if not data.get(field):
                raise ValidationError(f"Field '{field}' is required")
        
        # Validate transaction type
        valid_types = ['BUY', 'SELL', 'DIVIDEND', 'SPLIT', 'BONUS']
        if data['type'] not in valid_types:
            raise ValidationError(f"type must be one of: {', '.join(valid_types)}")
        
        # Validate security exists
        security = Security.query.get(data['security_id'])
        if not security:
            raise NotFoundError(f"Security {data['security_id']} not found")
        
        # Parse and validate transaction date
        try:
            transaction_date = datetime.strptime(data['transaction_date'], '%Y-%m-%d').date()
        except ValueError:
            raise ValidationError("transaction_date must be in YYYY-MM-DD format")
        
        # Validate quantity and price
        try:
            quantity = float(data['quantity'])
            price = float(data['price'])
        except (ValueError, TypeError):
            raise ValidationError("quantity and price must be valid numbers")
        
        if quantity <= 0:
            raise ValidationError("quantity must be positive")
        
        if price < 0:
            raise ValidationError("price cannot be negative")
        
        # Calculate amount if not provided
        if data.get('amount') is not None:
            amount = float(data['amount'])
        else:
            # For SPLIT and BONUS, amount is 0 as no money changes hands
            if data['type'] in ['SPLIT', 'BONUS']:
                amount = 0
            else:
                amount = quantity * price
        
        try:
            # Create transaction record
            transaction = Transaction(
                client_id=client_id,
                security_id=data['security_id'],
                type=data['type'],
                quantity=quantity,
                price=price,
                amount=amount,
                transaction_date=transaction_date,
                created_at=datetime.utcnow()
            )
            
            db.session.add(transaction)
            db.session.flush()  # Get the transaction ID
            
            # Update holdings using existing function
            holdings_result = update_holdings_from_transactions_api(client_id)
            if not holdings_result['success']:
                raise Exception(f"Holdings update failed: {holdings_result['message']}")
            
            # Update cashflows using existing function
            cashflow_result = update_cashflows_from_transactions_api(client_id)
            if not cashflow_result['success']:
                raise Exception(f"Cashflow update failed: {cashflow_result['message']}")
            
            # Commit the transaction
            db.session.commit()
            
            # Return success response
            return APIResponse.success(
                data={
                    'transaction': {
                        'id': transaction.id,
                        'client_id': transaction.client_id,
                        'security_id': transaction.security_id,
                        'security_name': security.name,
                        'type': transaction.type,
                        'quantity': float(transaction.quantity),
                        'price': float(transaction.price),
                        'amount': float(transaction.amount),
                        'transaction_date': transaction.transaction_date.isoformat(),
                        'notes': None
                    },
                    'holdings_updated': holdings_result['data'],
                    'cashflows_updated': cashflow_result['data']
                },
                message="Transaction created and portfolio updated successfully",
                status_code=201
            )
            
        except Exception as e:
            # Rollback on any failure
            db.session.rollback()
            raise Exception(f"Transaction processing failed: {str(e)}")
        
    except Exception as e:
        logger.error(f"Error creating transaction for client {client_id}: {str(e)}")
        raise

@transactions_bp.route('/<int:client_id>/transactions/<int:transaction_id>', methods=['PUT'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=20)
def update_transaction(client_id, transaction_id):
    """
    Update an existing transaction and recalculate holdings and cashflows
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Verify transaction exists and belongs to client
        transaction = Transaction.query.filter_by(
            id=transaction_id, 
            client_id=client_id
        ).first_or_404()
        
        data = request.get_json()
        
        try:
            # Update transaction fields if provided
            if 'security_id' in data:
                security = Security.query.get(data['security_id'])
                if not security:
                    raise NotFoundError(f"Security {data['security_id']} not found")
                transaction.security_id = data['security_id']
            
            if 'type' in data:
                valid_types = ['BUY', 'SELL', 'DIVIDEND', 'SPLIT', 'BONUS']
                if data['type'] not in valid_types:
                    raise ValidationError(f"type must be one of: {', '.join(valid_types)}")
                transaction.type = data['type']
            
            if 'quantity' in data:
                quantity = float(data['quantity'])
                if quantity <= 0:
                    raise ValidationError("quantity must be positive")
                transaction.quantity = quantity
            
            if 'price' in data:
                price = float(data['price'])
                if price < 0:
                    raise ValidationError("price cannot be negative")
                transaction.price = price
            
            if 'transaction_date' in data:
                try:
                    transaction_date = datetime.strptime(data['transaction_date'], '%Y-%m-%d').date()
                    transaction.transaction_date = transaction_date
                except ValueError:
                    raise ValidationError("transaction_date must be in YYYY-MM-DD format")
            
            if 'notes' in data:
                # Notes field not available in Transaction model
                pass
            
            # Recalculate amount
            if transaction.type in ['SPLIT', 'BONUS']:
                transaction.amount = 0
            else:
                transaction.amount = transaction.quantity * transaction.price
            
            # Update holdings using existing function
            holdings_result = update_holdings_from_transactions_api(client_id)
            if not holdings_result['success']:
                raise Exception(f"Holdings update failed: {holdings_result['message']}")
            
            # Update cashflows using existing function
            cashflow_result = update_cashflows_from_transactions_api(client_id)
            if not cashflow_result['success']:
                raise Exception(f"Cashflow update failed: {cashflow_result['message']}")
            
            # Commit the transaction
            db.session.commit()
            
            return APIResponse.success(
                data={
                    'transaction': {
                        'id': transaction.id,
                        'client_id': transaction.client_id,
                        'security_id': transaction.security_id,
                        'security_name': transaction.security.name if transaction.security else None,
                        'type': transaction.type,
                        'quantity': float(transaction.quantity),
                        'price': float(transaction.price),
                        'amount': float(transaction.amount),
                        'transaction_date': transaction.transaction_date.isoformat(),
                        'notes': None
                    },
                    'holdings_updated': holdings_result['data'],
                    'cashflows_updated': cashflow_result['data']
                },
                message="Transaction updated and portfolio recalculated successfully"
            )
            
        except Exception as e:
            # Rollback on any failure
            db.session.rollback()
            raise Exception(f"Transaction update failed: {str(e)}")
        
    except Exception as e:
        logger.error(f"Error updating transaction {transaction_id} for client {client_id}: {str(e)}")
        raise

@transactions_bp.route('/<int:client_id>/transactions/<int:transaction_id>', methods=['DELETE'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=10)
def delete_transaction(client_id, transaction_id):
    """
    Delete a transaction and recalculate holdings and cashflows
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Verify transaction exists and belongs to client
        transaction = Transaction.query.filter_by(
            id=transaction_id, 
            client_id=client_id
        ).first_or_404()
        
        try:
            # Delete the transaction
            db.session.delete(transaction)
            
            # Update holdings using existing function
            holdings_result = update_holdings_from_transactions_api(client_id)
            if not holdings_result['success']:
                raise Exception(f"Holdings update failed: {holdings_result['message']}")
            
            # Update cashflows using existing function
            cashflow_result = update_cashflows_from_transactions_api(client_id)
            if not cashflow_result['success']:
                raise Exception(f"Cashflow update failed: {cashflow_result['message']}")
            
            # Commit the transaction
            db.session.commit()
            
            return APIResponse.success(
                data={
                    'deleted_transaction_id': transaction_id,
                    'holdings_updated': holdings_result['data'],
                    'cashflows_updated': cashflow_result['data']
                },
                message="Transaction deleted and portfolio recalculated successfully"
            )
            
        except Exception as e:
            # Rollback on any failure
            db.session.rollback()
            raise Exception(f"Transaction deletion failed: {str(e)}")
        
    except Exception as e:
        logger.error(f"Error deleting transaction {transaction_id} for client {client_id}: {str(e)}")
        raise

# ============================================================================
# BULK TRANSACTION OPERATIONS
# ============================================================================


def _process_bulk_transactions(client_id, transactions_data):
    """
    Internal helper function to process bulk transactions.
    Can be called directly with transactions_data list.
    """
    if not isinstance(transactions_data, list):
        raise ValidationError("transactions must be an array")
    
    if len(transactions_data) == 0:
        raise ValidationError("transactions array cannot be empty")
    
    if len(transactions_data) > 1000:
        raise ValidationError("Cannot process more than 1000 transactions at once")
    
    try:
        created_transactions = []
        
        # Process each transaction
        for i, transaction_data in enumerate(transactions_data):
            # Validate required fields
            required_fields = ['security_id', 'type', 'quantity', 'transaction_date']
            for field in required_fields:
                if not transaction_data.get(field):
                    raise ValidationError(f"Transaction {i+1}: Field '{field}' is required")
            
            # Check price field exists (but allow 0 for SPLIT/BONUS)
            if 'price' not in transaction_data:
                raise ValidationError(f"Transaction {i+1}: Field 'price' is required")
            
            # Validate transaction type
            valid_types = ['BUY', 'SELL', 'DIVIDEND', 'SPLIT', 'BONUS']
            if transaction_data['type'] not in valid_types:
                raise ValidationError(f"Transaction {i+1}: type must be one of: {', '.join(valid_types)}")
            
            # Validate security exists
            security = Security.query.get(transaction_data['security_id'])
            if not security:
                raise NotFoundError(f"Transaction {i+1}: Security {transaction_data['security_id']} not found")
            
            # Parse transaction date - ensure it's a date then combine with midnight (IST calendar date with no TZ shift)
            try:
                date_str = transaction_data['transaction_date']
                if isinstance(date_str, str):
                    parsed_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                elif isinstance(date_str, date):
                    parsed_date = date_str
                else:
                    # Try to extract date from datetime
                    parsed_date = date_str.date() if hasattr(date_str, 'date') else date.today()
                transaction_date = datetime.combine(parsed_date, time(0, 0, 0))
            except (ValueError, TypeError) as e:
                raise ValidationError(f"Transaction {i+1}: transaction_date must be in YYYY-MM-DD format. Error: {str(e)}")
            
            # Validate quantity and price
            try:
                quantity = float(transaction_data['quantity'])
                price = float(transaction_data['price'])
            except (ValueError, TypeError):
                raise ValidationError(f"Transaction {i+1}: quantity and price must be valid numbers")
            
            if quantity <= 0:
                raise ValidationError(f"Transaction {i+1}: quantity must be positive")
            
            if price < 0:
                raise ValidationError(f"Transaction {i+1}: price cannot be negative")
            
            # Calculate amount
            if transaction_data['type'] in ['SPLIT', 'BONUS']:
                amount = 0
            else:
                amount = quantity * price
            
            # Create transaction
            transaction = Transaction(
                client_id=client_id,
                security_id=transaction_data['security_id'],
                type=transaction_data['type'],
                quantity=quantity,
                price=price,
                amount=amount,
                transaction_date=transaction_date,
                created_at=datetime.utcnow()
            )
            
            db.session.add(transaction)
            created_transactions.append(transaction)
        
        # Update holdings using existing function
        holdings_result = update_holdings_from_transactions_api(client_id)
        if not holdings_result['success']:
            raise Exception(f"Holdings update failed: {holdings_result['message']}")
        
        # Update cashflows using existing function
        cashflow_result = update_cashflows_from_transactions_api(client_id)
        if not cashflow_result['success']:
            raise Exception(f"Cashflow update failed: {cashflow_result['message']}")
        
        # Commit the transaction
        db.session.commit()
        
        # Format response
        transactions_response = []
        for transaction in created_transactions:
            transactions_response.append({
                'id': transaction.id,
                'security_id': transaction.security_id,
                'security_name': transaction.security.name if transaction.security else None,
                'type': transaction.type,
                'quantity': float(transaction.quantity),
                'price': float(transaction.price),
                'amount': float(transaction.amount),
                'transaction_date': transaction.transaction_date.isoformat(),
                'notes': None
            })
        
        return {
            'transactions': transactions_response,
            'count': len(created_transactions),
            'holdings_updated': holdings_result['data'],
            'cashflows_updated': cashflow_result['data']
        }
    except Exception as e:
        # Rollback on any failure
        db.session.rollback()
        raise Exception(f"Bulk transaction processing failed: {str(e)}")


def create_bulk_transactions(client_id):
    """
    Create multiple transactions in a single operation
    """
    try:
        data = request.get_json()
        
        if not data.get('transactions'):
            raise ValidationError("transactions array is required")
        
        transactions_data = data['transactions']
        
        result_data = _process_bulk_transactions(client_id, transactions_data)
        
        return APIResponse.success(
            data=result_data,
            message=f"Successfully created {result_data['count']} transactions",
            status_code=201
        )
        
    except Exception as e:
        logger.error(f"Error creating bulk transactions for client {client_id}: {str(e)}")
        raise

@transactions_bp.route('/<int:client_id>/transactions/preview', methods=['POST'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=5)
def preview_transactions_file(client_id):
    """
    Preview transactions from uploaded file before processing
    """
    try:
        logger.info(f"=== PREVIEW API CALLED ===")
        logger.info(f"Client ID: {client_id}")
        logger.info(f"Request method: {request.method}")
        logger.info(f"Request files: {list(request.files.keys())}")
        logger.info(f"Request form: {dict(request.form)}")
        
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        logger.info(f"Client found: {client.name if client else 'Not found'}")
        
        # Check if file is present
        if 'file' not in request.files:
            logger.error("No file provided in request")
            raise ValidationError("No file provided")
        
        file = request.files['file']
        logger.info(f"PREVIEW API INPUT - Client: {client_id}, File: {file.filename}")
        
        if file.filename == '':
            raise ValidationError("No file selected")
        
        # Check file extension
        from werkzeug.utils import secure_filename
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(('.xlsx', '.xls', '.csv')):
            raise ValidationError("Please upload an Excel (.xlsx, .xls) or CSV file")
        
        # Read the file
        try:
            import pandas as pd
            if filename.lower().endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
        except Exception as e:
            raise ValidationError(f"Error reading file: {str(e)}")
        
        # Use enhanced validation for better error reporting
        from enhanced_transaction_validation import EnhancedTransactionValidator
        validator = EnhancedTransactionValidator(db.session)
        
        validation_result = validator.validate_file(df)
        
        # Check if there are any valid transactions
        if validation_result['valid_count'] == 0:
            # No valid transactions - return error with details
            error_details = {
                'file_errors': validation_result['errors'],
                'warnings': validation_result['warnings'],
                'total_rows': validation_result['total_rows'],
                'valid_count': validation_result['valid_count'],
                'error_count': validation_result['error_count']
            }
            
            # Format errors for better display
            formatted_errors = []
            for error in validation_result['errors']:
                if isinstance(error, str) and error.startswith('Missing required columns'):
                    formatted_errors.append({
                        'type': 'file_structure',
                        'message': error,
                        'suggestion': 'Please check your column headers and ensure they match exactly.'
                    })
                elif 'Row' in error and ':' in error:
                    try:
                        row_num = error.split('Row ')[1].split(':')[0]
                        formatted_errors.append({
                            'type': 'row_validation',
                            'row': int(row_num),
                            'message': error,
                            'suggestion': _get_error_suggestion(error)
                        })
                    except:
                        formatted_errors.append({
                            'type': 'row_validation',
                            'message': error,
                            'suggestion': 'Please check the data in this row.'
                        })
                else:
                    formatted_errors.append({
                        'type': 'general',
                        'message': error,
                        'suggestion': 'Please check your file format and data.'
                    })
            
            return APIResponse.error(
                message=f'File validation failed with {validation_result["error_count"]} errors',
                status_code=400,
                details={
                    'validation_errors': formatted_errors,
                    'summary': error_details
                }
            )
        
        # Process valid transactions for preview using enhanced validation results
        preview_data = []
        errors = validation_result['errors']
        special_date_warning = False
        
        # Convert enhanced validation results to preview format
        for i, transaction_data in enumerate(validation_result['valid_transactions']):
            # Get security details
            security = Security.query.get(transaction_data['security_id'])
            
            preview_data.append({
                'row_number': i + 2,  # +2 for header and 0-based index
                'security_id': transaction_data['security_id'],
                'security_symbol': security.symbol if security else 'Unknown',
                'security_name': security.name if security else 'Unknown',
                'type': transaction_data['type'],
                'quantity': transaction_data['quantity'],
                'price': transaction_data['price'],
                'amount': transaction_data['amount'],
                'transaction_date': transaction_data['transaction_date'].isoformat(),
                'selected': True
            })
        
        # Old inline validation loop removed in favor of enhanced validation results above
        
        # Return preview data
        response_data = {
            'preview_transactions': preview_data,
            'total_rows': len(df),
            'valid_transactions': len(preview_data),
            'errors': errors,
            'error_count': len(errors),
            'file_name': filename,
            'special_date_warning': special_date_warning
        }
        
        # Add warning message if special date found
        if special_date_warning:
            response_data['warning_message'] = "⚠️ WARNING: We do not have exact date of all trades. Please update the cashflow dates to get correct performance details."
        logger.info(f"PREVIEW API OUTPUT - Valid: {len(preview_data)}, Errors: {len(errors)}, Data: {response_data}")
        
        return APIResponse.success(
            data=response_data,
            message=f"Preview generated. {len(preview_data)} valid transactions found.",
            status_code=200
        )
        
    except Exception as e:
        logger.error(f"Error previewing transactions file for client {client_id}: {str(e)}")
        return APIResponse.error(
            message=f"Error previewing transactions: {str(e)}",
            status_code=500
        )

@transactions_bp.route('/<int:client_id>/transactions/process', methods=['POST'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=5)
def process_selected_transactions(client_id):
    """
    Process selected transactions from preview
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get selected transactions from request
        data = request.get_json()
        logger.info(f"PROCESS API INPUT - Client: {client_id}, Data: {data}")
        
        if not data or 'transactions' not in data:
            raise ValidationError("No transactions data provided")
        
        selected_transactions = data['transactions']
        if not selected_transactions:
            raise ValidationError("No transactions selected for processing")
        
        # Validate that all transactions have required fields
        for i, transaction in enumerate(selected_transactions):
            required_fields = ['security_id', 'type', 'quantity', 'price', 'transaction_date']
            missing_fields = [field for field in required_fields if field not in transaction]
            if missing_fields:
                raise ValidationError(f"Transaction {i+1} missing required fields: {', '.join(missing_fields)}")
        
        # Process transactions using the shared helper function
        result_data = _process_bulk_transactions(client_id, selected_transactions)
        
        logger.info(f"PROCESS API OUTPUT - Processed {result_data['count']} transactions")
        return APIResponse.success(
            data=result_data,
            message=f"Successfully processed {result_data['count']} transactions",
            status_code=201
        )
        
    except Exception as e:
        logger.error(f"Error processing selected transactions for client {client_id}: {str(e)}")
        return APIResponse.error(
            message=f"Error processing transactions: {str(e)}",
            status_code=500
        )

@transactions_bp.route('/<int:client_id>/transactions/upload-enhanced', methods=['POST'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=5)
def upload_transactions_file_enhanced(client_id):
    """
    Upload and process transaction file with enhanced error reporting
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Check if file is present
        if 'file' not in request.files:
            raise ValidationError("No file provided")
        
        file = request.files['file']
        if file.filename == '':
            raise ValidationError("No file selected")
        
        # Check file extension
        from werkzeug.utils import secure_filename
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(('.xlsx', '.xls', '.csv')):
            raise ValidationError("Please upload an Excel (.xlsx, .xls) or CSV file")
        
        # Read the file
        try:
            import pandas as pd
            if filename.lower().endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
        except Exception as e:
            raise ValidationError(f"Error reading file: {str(e)}")
        
        # Use enhanced validation
        from enhanced_transaction_validation import EnhancedTransactionValidator
        validator = EnhancedTransactionValidator(db.session)
        
        validation_result = validator.validate_file(df)
        
        # Always return preview data (even if there are errors)
        error_details = {
            'file_errors': validation_result['errors'],
            'warnings': validation_result['warnings'],
            'total_rows': validation_result['total_rows'],
            'valid_count': validation_result['valid_count'],
            'error_count': validation_result['error_count']
        }
        
        # Format errors for better display
        formatted_errors = []
        for error in validation_result['errors']:
            if isinstance(error, str) and error.startswith('Missing required columns'):
                formatted_errors.append({
                    'type': 'file_structure',
                    'message': error,
                    'suggestion': 'Please check your column headers and ensure they match exactly.'
                })
            elif 'Row' in error and ':' in error:
                try:
                    row_num = error.split('Row ')[1].split(':')[0]
                    formatted_errors.append({
                        'type': 'row_validation',
                        'row': int(row_num),
                        'message': error,
                        'suggestion': _get_error_suggestion(error)
                    })
                except:
                    formatted_errors.append({
                        'type': 'row_validation',
                        'message': error,
                        'suggestion': 'Please check the data in this row.'
                    })
            else:
                formatted_errors.append({
                    'type': 'general',
                    'message': error,
                    'suggestion': 'Please check your file format and data.'
                })
        
        # Return preview data
        return APIResponse.success(
            message=f'File preview ready: {validation_result["valid_count"]} valid transactions, {validation_result["error_count"]} errors',
            data={
                'preview_mode': True,
                'valid_transactions': validation_result['valid_transactions'],
                'validation_errors': formatted_errors,
                'summary': error_details,
                'client_id': client_id
            }
        )
        
        # Process valid transactions
        transactions_data = []
        for transaction_data in validation_result['valid_transactions']:
            transactions_data.append({
                'security_id': transaction_data['security_id'],
                'type': transaction_data['type'],
                'quantity': transaction_data['quantity'],
                'price': transaction_data['price'],
                'amount': transaction_data['amount'],
                'transaction_date': transaction_data['transaction_date'].isoformat(),
                'notes': f'Uploaded from file: {filename}'
            })
        
        # Create transactions using bulk endpoint
        from flask import current_app
        with current_app.test_request_context(
            f'/api/v1/clients/{client_id}/transactions/bulk',
            method='POST',
            json={'transactions': transactions_data}
        ):
            result = create_bulk_transactions(client_id)
        
        # Add validation summary to result
        if result.get('success'):
            result['data']['validation_summary'] = {
                'total_rows': validation_result['total_rows'],
                'valid_transactions': validation_result['valid_count'],
                'warnings': validation_result['warnings']
            }
        
        return result
        
    except Exception as e:
        logger.error(f"Error in enhanced upload for client {client_id}: {str(e)}")
        return APIResponse.error(
            message=f"Error processing file: {str(e)}",
            status_code=500
        )


@transactions_bp.route('/<int:client_id>/transactions/upload-mprofit', methods=['POST'])
def upload_transactions_file_mprofit(client_id):
    """
    Upload and process a trade file exported from MProfit.
    Step 1: Convert MProfit format to standard format
    Step 2: Handle symbol resolution (map company names to NSE symbols)
    Step 3: Once resolved, process using standard upload logic
    """
    try:
        Client.query.get_or_404(client_id)

        if 'file' not in request.files:
            raise ValidationError("No file provided")

        file = request.files['file']
        if not file.filename:
            raise ValidationError("No file selected")

        overrides = {}
        raw_resolutions = request.form.get('resolutions')
        if raw_resolutions:
            try:
                overrides = json.loads(raw_resolutions)
            except json.JSONDecodeError:
                raise ValidationError("Invalid resolutions payload; expected JSON object.")

        # Step 1: Convert MProfit format to standard format (Date, Type, Stock, Transacted Units, Transacted Price)
        service = MProfitTradeImportService(db.session)
        conversion_result = service.convert_file(file, overrides=overrides)
        db.session.flush()

        summary = {
            'total_rows': conversion_result.total_rows,
            'converted_rows': conversion_result.processed_rows,
            'missing_symbol_count': len(conversion_result.missing_symbols),
            'conversion_error_count': len(conversion_result.conversion_errors),
        }

        # Step 2: If symbols need resolution, return them for UI
        # Also show conversion errors if any (but don't block if we have missing symbols)
        if conversion_result.missing_symbols:
            db.session.commit()
            return APIResponse.success(
                message="Symbol resolution required. Please map company names to NSE symbols.",
                data={
                    'status': 'needs_resolution',
                    'missing_symbols': _format_mprofit_missing_symbols(conversion_result.missing_symbols),
                    'auto_matched_symbols': conversion_result.auto_matched_symbols,  # Show auto-matched symbols
                    'conversion_errors': conversion_result.conversion_errors[:20],  # Show first 20 errors
                    'conversion_error_count': len(conversion_result.conversion_errors),
                    'summary': summary,
                }
            )

        # Step 3: If no trades converted, return error with helpful details
        # Note: Dividend, bonus, split, etc. transactions are automatically skipped
        if conversion_result.converted.empty:
            db.session.commit()
            error_message = "No BUY/SELL trades were successfully converted from the file."
            if conversion_result.conversion_errors:
                # Show first few errors to help debug
                sample_errors = conversion_result.conversion_errors[:5]
                error_message += f" Found {len(conversion_result.conversion_errors)} conversion errors."
                if len(conversion_result.conversion_errors) > 0:
                    error_message += f" Sample errors: {'; '.join(sample_errors)}"
            
            if summary.get('total_rows', 0) > 0 and summary.get('converted_rows', 0) == 0:
                error_message += " All BUY/SELL transactions failed validation. Please check the conversion errors below."
            
            return APIResponse.error(
                message=error_message,
                status_code=400,
                details={
                    'conversion_errors': conversion_result.conversion_errors[:50],  # Show first 50 errors
                    'conversion_error_count': len(conversion_result.conversion_errors),
                    'summary': summary,
                    'suggestion': 'Please review the conversion errors above. Common issues: date format (expected DD/MM/YYYY), missing asset names, or invalid quantity/price values. Dividend, bonus, split transactions are automatically skipped.'
                }
            )

        # Step 4: All symbols resolved - prepare preview for user review
        from werkzeug.utils import secure_filename
        filename = secure_filename(file.filename)

        # Use the same processing logic as regular upload
        transactions_data, errors = _process_transactions_dataframe(
            client_id,
            conversion_result.converted,
            f'MProfit: {filename}'
        )

        # Combine conversion errors with processing errors
        all_errors = conversion_result.conversion_errors + errors

        if all_errors:
            db.session.commit()
            return APIResponse.error(
                message=f'File processing failed with {len(all_errors)} errors.',
                details={
                    'file_name': filename,
                    'total_rows': conversion_result.total_rows,
                    'validation_errors': all_errors,
                    'error_count': len(all_errors),
                    'valid_transactions': len(transactions_data),
                    'conversion_errors': conversion_result.conversion_errors,
                    'processing_errors': errors,
                },
                status_code=400
            )

        if not transactions_data:
            db.session.commit()
            return APIResponse.error(
                message='No valid transactions found after conversion.',
                status_code=400,
                details={
                    'conversion_errors': conversion_result.conversion_errors,
                    'summary': summary,
                }
            )

        # Step 5: Return preview data for user review instead of directly creating transactions
        db.session.commit()
        
        # Format transactions for preview (include security symbol and name)
        preview_transactions = []
        for txn_data in transactions_data:
            security = Security.query.get(txn_data['security_id'])
            preview_transactions.append({
                'security_id': txn_data['security_id'],
                'security_symbol': security.symbol if security else 'N/A',
                'security_name': security.name if security else 'N/A',
                'type': txn_data['type'],
                'quantity': float(txn_data['quantity']),
                'price': float(txn_data['price']),
                'amount': float(txn_data['amount']),
                'transaction_date': txn_data['transaction_date'],
            })
        
        return APIResponse.success(
            message="File converted successfully. Please review the transactions below before confirming upload.",
            data={
                'status': 'preview_ready',
                'file_name': filename,
                'total_rows': conversion_result.total_rows,
                'valid_transactions': preview_transactions,
                'validation_errors': all_errors if all_errors else [],
                'auto_matched_symbols': conversion_result.auto_matched_symbols,
                'summary': summary,
            }
        )

    except ValidationError as ve:
        db.session.rollback()
        return APIResponse.error(message=str(ve), status_code=400)
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in MProfit upload for client {client_id}: {str(e)}")
        return APIResponse.error(
            message=f"Error processing MProfit file: {str(e)}",
            status_code=500
        )


@transactions_bp.route('/mprofit-symbols/pending', methods=['GET'])
def list_pending_mprofit_symbols():
    """List unresolved MProfit symbol mappings."""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 200)
        search_query = request.args.get('q', '').strip()

        query = MProfitSymbolMap.query.filter(MProfitSymbolMap.security_id.is_(None))

        if search_query:
            like = f"%{search_query}%"
            query = query.filter(or_(
                MProfitSymbolMap.raw_name.ilike(like),
                MProfitSymbolMap.normalized_name.ilike(like)
            ))

        total = query.count()
        mappings = query.order_by(MProfitSymbolMap.created_at.desc()).offset(
            (page - 1) * per_page
        ).limit(per_page).all()

        data = [
            {
                'id': mapping.id,
                'raw_name': mapping.raw_name,
                'normalized_name': mapping.normalized_name,
                'nse_symbol': mapping.nse_symbol,
                'notes': mapping.notes,
                'created_at': mapping.created_at.isoformat() if mapping.created_at else None,
                'updated_at': mapping.updated_at.isoformat() if mapping.updated_at else None,
                'last_used_at': mapping.last_used_at.isoformat() if mapping.last_used_at else None,
            }
            for mapping in mappings
        ]

        return APIResponse.paginated(
            data=data,
            page=page,
            per_page=per_page,
            total=total,
            message=f"Retrieved {len(data)} pending mappings"
        )
    except Exception as e:
        logger.error(f"Error fetching pending MProfit mappings: {str(e)}")
        return APIResponse.error(
            message=f"Error fetching pending mappings: {str(e)}",
            status_code=500
        )


@transactions_bp.route('/mprofit-symbols/<int:mapping_id>', methods=['PUT'])
def update_mprofit_symbol_mapping(mapping_id):
    """Update an MProfit symbol mapping with the resolved NSE symbol/security ID."""
    try:
        # Check authentication - return JSON error instead of redirect
        if not current_user.is_authenticated:
            return APIResponse.error(
                message="Authentication required",
                status_code=401,
                error_code="UNAUTHORIZED"
            )
        
        if not request.is_json:
            raise ValidationError("Request must be JSON")

        payload = request.get_json() or {}
        security_id = payload.get('security_id')
        nse_symbol = payload.get('nse_symbol')
        notes = payload.get('notes')

        if not security_id and not nse_symbol:
            raise ValidationError("Provide either 'security_id' or 'nse_symbol'")

        mapping = MProfitSymbolMap.query.get_or_404(mapping_id)
        mapping.normalized_name = MProfitTradeImportService.normalize_asset_name(mapping.raw_name)

        security = None
        if security_id:
            security = Security.query.get(security_id)
            if not security:
                raise NotFoundError(f"Security {security_id} not found")
        else:
            symbol = str(nse_symbol).strip().upper()
            # Try exact match first
            security = Security.query.filter_by(symbol=symbol).first()
            # If not found, try case-insensitive match
            if not security:
                security = Security.query.filter(Security.symbol.ilike(symbol)).first()
            
            # If still not found, try partial/fuzzy matching and provide suggestions
            if not security:
                # First, check if there's an exact match (case-insensitive) that we might have missed
                # This handles cases where the symbol exists but with different casing
                exact_match = Security.query.filter(
                    func.upper(Security.symbol) == symbol.upper()
                ).first()
                if exact_match:
                    security = exact_match
                else:
                    # Try partial match (symbol starts with the search term)
                    # Also include exact match in case it exists but wasn't caught earlier
                    partial_matches = Security.query.filter(
                        or_(
                            Security.symbol.ilike(f'{symbol}%'),
                            func.upper(Security.symbol) == symbol.upper()
                        )
                    ).limit(10).all()
                    
                    # Also try searching by name if symbol doesn't match
                    name_matches = Security.query.filter(
                        Security.name.ilike(f'%{symbol}%')
                    ).limit(5).all()
                    
                    # Build helpful error message with suggestions
                    # Use a set to track symbols we've already added to avoid duplicates
                    seen_symbols = set()
                    suggestions = []
                    
                    # Add partial matches first (symbols starting with the search term)
                    if partial_matches:
                        for s in partial_matches[:5]:
                            if s.symbol not in seen_symbols:
                                suggestions.append(s.symbol)
                                seen_symbols.add(s.symbol)
                    
                    # Add name matches (but skip if we already have the symbol)
                    if name_matches:
                        for s in name_matches[:3]:
                            if s.symbol not in seen_symbols:
                                suggestions.append(f"{s.symbol} ({s.name})")
                                seen_symbols.add(s.symbol)
                    
                    # Security doesn't exist - create it automatically for historical trades
                    # This allows recording old trades for merged/delisted securities
                    logger.info(f"Creating new security '{symbol}' for historical trade recording")
                    
                    # Get default asset class (usually ID 1 for Equity/Stocks)
                    from models import AssetClass
                    default_asset_class = AssetClass.query.filter_by(name='Equity').first()
                    if not default_asset_class:
                        # Fallback to first asset class if 'Equity' doesn't exist
                        default_asset_class = AssetClass.query.first()
                    if not default_asset_class:
                        raise ValidationError("No asset class found. Please create an asset class first.")
                    
                    # Create the security with user-provided symbol
                    security = Security(
                        symbol=symbol,
                        name=f"{symbol} (Historical - Merged/Delisted)",  # Indicate it's historical
                        asset_class_id=default_asset_class.id,
                        security_type='STOCK',
                        current_price=0.0,
                        refresh_interval=300,
                        meta_data=json.dumps({
                            'source': 'mprofit_manual_entry',
                            'note': 'Created automatically for historical trade recording',
                            'original_company': mapping.raw_name if mapping else symbol
                        }),
                        created_by=current_user.id
                    )
                    db.session.add(security)
                    db.session.flush()  # Flush to get the ID
                    logger.info(f"Created security '{symbol}' with ID {security.id} for historical trade")

        mapping.security_id = security.id
        mapping.nse_symbol = security.symbol
        mapping.is_manual = True
        mapping.notes = notes
        mapping.last_used_at = datetime.utcnow()
        mapping.updated_at = datetime.utcnow()

        db.session.commit()

        return APIResponse.success(
            message="Mapping updated successfully",
            data={
                'id': mapping.id,
                'raw_name': mapping.raw_name,
                'normalized_name': mapping.normalized_name,
                'nse_symbol': mapping.nse_symbol,
                'security_id': mapping.security_id,
                'notes': mapping.notes,
                'last_used_at': mapping.last_used_at.isoformat() if mapping.last_used_at else None,
            }
        )
    except ValidationError as ve:
        db.session.rollback()
        return APIResponse.error(message=str(ve), status_code=400)
    except NotFoundError as ne:
        db.session.rollback()
        return APIResponse.error(message=str(ne), status_code=404)
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating MProfit mapping {mapping_id}: {str(e)}")
        return APIResponse.error(
            message=f"Error updating mapping: {str(e)}",
            status_code=500
        )


@transactions_bp.route('/mprofit-symbols/<int:mapping_id>', methods=['DELETE'])
def clear_mprofit_symbol_mapping(mapping_id):
    """Clear/reset an MProfit symbol mapping so it can be manually resolved."""
    try:
        # Check authentication - return JSON error instead of redirect
        if not current_user.is_authenticated:
            return APIResponse.error(
                message="Authentication required",
                status_code=401,
                error_code="UNAUTHORIZED"
            )
        
        mapping = MProfitSymbolMap.query.get_or_404(mapping_id)
        
        # Clear the mapping (set to unresolved)
        mapping.security_id = None
        mapping.nse_symbol = None
        mapping.is_manual = False
        mapping.notes = f"Cleared for manual correction (was: {mapping.notes or 'auto-matched'})"
        mapping.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        return APIResponse.success(
            message="Mapping cleared successfully. It can now be manually resolved.",
            data={
                'id': mapping.id,
                'raw_name': mapping.raw_name,
                'normalized_name': mapping.normalized_name,
            }
        )
    except NotFoundError as ne:
        db.session.rollback()
        return APIResponse.error(message=str(ne), status_code=404)
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error clearing MProfit mapping {mapping_id}: {str(e)}")
        return APIResponse.error(
            message=f"Error clearing mapping: {str(e)}",
            status_code=500
        )


@transactions_bp.route('/mprofit-symbols/securities', methods=['GET'])
def search_securities_for_mprofit():
    """Search securities to resolve MProfit mappings."""
    try:
        # Check authentication - return JSON error instead of redirect
        if not current_user.is_authenticated:
            return APIResponse.error(
                message="Authentication required",
                status_code=401,
                error_code="UNAUTHORIZED"
            )
        
        query_param = request.args.get('q', '').strip()
        limit = min(request.args.get('limit', 20, type=int), 50)

        query = Security.query
        if query_param:
            like_value = f"%{query_param}%"
            query = query.filter(or_(
                Security.symbol.ilike(like_value),
                Security.name.ilike(like_value)
            ))

        securities = query.order_by(Security.symbol.asc()).limit(limit).all()

        results = [
            {
                'security_id': security.id,
                'symbol': security.symbol,
                'name': security.name,
            }
            for security in securities
        ]

        return APIResponse.success(
            message=f"Found {len(results)} securities",
            data=results
        )
    except Exception as e:
        logger.error(f"Error searching securities for MProfit mappings: {str(e)}")
        return APIResponse.error(
            message=f"Error searching securities: {str(e)}",
            status_code=500
        )


def _format_mprofit_missing_symbols(missing_symbols):
    formatted = []
    for item in missing_symbols:
        formatted.append({
            'raw_name': item.raw_name,
            'normalized_name': item.normalized_name,
            'mapping_id': item.mapping_id,
            'rows': item.rows,
            'suggestions': item.suggestions,
        })
    return formatted


def _format_validation_errors(errors: list):
    formatted_errors = []
    for error in errors:
        if isinstance(error, dict):
            formatted_errors.append(error)
            continue

        if isinstance(error, str) and error.startswith('Missing required columns'):
            formatted_errors.append({
                'type': 'file_structure',
                'message': error,
                'suggestion': 'Please check your column headers and ensure they match exactly.'
            })
        elif isinstance(error, str) and 'Row' in error and ':' in error:
            try:
                row_num = error.split('Row ')[1].split(':')[0]
                formatted_errors.append({
                    'type': 'row_validation',
                    'row': int(row_num),
                    'message': error,
                    'suggestion': _get_error_suggestion(error)
                })
            except Exception:
                formatted_errors.append({
                    'type': 'row_validation',
                    'message': error,
                    'suggestion': 'Please review this row in the source file.'
                })
        else:
            formatted_errors.append({
                'type': 'general',
                'message': error,
                'suggestion': 'Please check your file format and data.'
            })
    return formatted_errors


def _get_error_suggestion(error_message):
    """Get specific suggestions based on error message"""
    if 'date' in error_message.lower():
        return 'Please ensure the date is in a supported format: DD-MMM-YYYY, DD/MM/YYYY, YYYY-MM-DD, etc.'
    elif 'transaction type' in error_message.lower():
        return 'Valid transaction types are: BUY, SELL, SPLIT, BONUS (case insensitive).'
    elif 'security' in error_message.lower() and 'not found' in error_message.lower():
        return 'Please check if the stock symbol exists in the database or add it first.'
    elif 'quantity' in error_message.lower():
        return 'Quantity must be a positive number (no text or special characters).'
    elif 'price' in error_message.lower():
        return 'Price must be a non-negative number. Currency symbols will be removed automatically.'
    else:
        return 'Please check the data format and ensure all required fields are filled correctly.'


def _process_transactions_dataframe(client_id, df, filename='uploaded_file'):
    """
    Process a DataFrame with standard transaction format (Date, Type, Stock, Transacted Units, Transacted Price (per unit))
    Returns tuple: (transactions_data, errors)
    """
    import pandas as pd
    
    transactions_data = []
    errors = []
    
    # Validate required columns
    required_columns = ['Date', 'Type', 'Stock', 'Transacted Units', 'Transacted Price (per unit)']
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        raise ValidationError(f"Missing required columns: {', '.join(missing_columns)}")
    
    # Check file size (limit to 1000 rows)
    if len(df) > 1000:
        raise ValidationError(f"File too large. Maximum 1000 transactions allowed. Found {len(df)} transactions.")
    
    for index, row in df.iterrows():
        try:
            # Parse date
            if pd.isna(row['Date']):
                errors.append(f'Row {index + 2}: Date is missing or empty')
                continue
            
            # Handle different date formats
            if isinstance(row['Date'], str):
                date_formats = ['%d-%b-%Y', '%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%m/%d/%Y']
                transaction_date = None
                for fmt in date_formats:
                    try:
                        transaction_date = datetime.strptime(row['Date'], fmt).date()
                        break
                    except ValueError:
                        continue
                
                if not transaction_date:
                    errors.append(f'Row {index + 2}: Invalid date format "{row["Date"]}". Expected formats: DD-MMM-YYYY, DD/MM/YYYY, YYYY-MM-DD, DD-MM-YYYY, MM/DD/YYYY')
                    continue
            else:
                try:
                    # Use safe date parsing to avoid timezone conversion issues
                    from utils.date_utils import parse_date_safe
                    transaction_date = parse_date_safe(row['Date'])
                except Exception as e:
                    errors.append(f'Row {index + 2}: Cannot parse date "{row["Date"]}" - {str(e)}')
                    continue
            
            # Parse transaction type
            transaction_type = str(row['Type']).strip().upper()
            if transaction_type not in ['BUY', 'SELL', 'SPLIT', 'BONUS']:
                errors.append(f'Row {index + 2}: Invalid transaction type "{row["Type"]}". Valid types: BUY, SELL, SPLIT, BONUS')
                continue
            
            # Parse stock symbol
            stock_symbol = str(row['Stock']).strip()
            if pd.isna(stock_symbol) or stock_symbol == '':
                errors.append(f'Row {index + 2}: Stock symbol is missing or empty')
                continue
            
            # Clean stock symbol
            stock_symbol = re.sub(r'^(NSE:|BSE:)\s*', '', stock_symbol)
            
            # Find security
            security = Security.query.filter_by(symbol=stock_symbol).first()
            
            if not security:
                security = Security.query.filter(
                    Security.symbol.ilike(f'%{stock_symbol}%')
                ).first()
            
            if not security:
                security = Security.query.filter(
                    Security.symbol.ilike(stock_symbol)
                ).first()
            
            if not security:
                errors.append(f'Row {index + 2}: Security "{stock_symbol}" not found in database. Please check the symbol or add the security first.')
                continue
            
            # Parse quantity and price
            try:
                quantity = float(row['Transacted Units'])
                price_str = str(row['Transacted Price (per unit)'])
                price_str = re.sub(r'[₹$,]', '', price_str).strip()
                price = float(price_str)
            except (ValueError, TypeError) as e:
                errors.append(f'Row {index + 2}: Invalid quantity or price format - Quantity: "{row.get("Transacted Units", "N/A")}", Price: "{row.get("Transacted Price (per unit)", "N/A")}". Expected numeric values.')
                continue
            
            # Validate quantity and price
            if pd.isna(quantity) or quantity <= 0:
                errors.append(f'Row {index + 2}: Quantity must be positive (found: {row.get("Transacted Units", "N/A")})')
                continue
            
            if pd.isna(price) or price < 0:
                errors.append(f'Row {index + 2}: Price cannot be negative (found: {row.get("Transacted Price (per unit)", "N/A")})')
                continue
            
            if transaction_type in ['SPLIT', 'BONUS'] and price != 0:
                errors.append(f'Row {index + 2}: Price should be 0 for {transaction_type} transactions (found: {price})')
                continue
            
            # Calculate amount
            if transaction_type in ['SPLIT', 'BONUS']:
                amount = 0
            else:
                amount = quantity * price
            
            # Add to transactions data
            transactions_data.append({
                'security_id': security.id,
                'type': transaction_type,
                'quantity': quantity,
                'price': price,
                'amount': amount,
                'transaction_date': transaction_date.isoformat(),
                'notes': f'Uploaded from file: {filename}'
            })
            
        except Exception as e:
            errors.append(f'Row {index + 2}: Unexpected error - {str(e)}')
            continue
    
    return transactions_data, errors


@transactions_bp.route('/<int:client_id>/transactions/upload', methods=['POST'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=5)
def upload_transactions_file(client_id):
    """
    Upload and process transaction file (Excel/CSV) via API
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Check if file is present
        if 'file' not in request.files:
            raise ValidationError("No file provided")
        
        file = request.files['file']
        if file.filename == '':
            raise ValidationError("No file selected")
        
        # Check file extension
        from werkzeug.utils import secure_filename
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(('.xlsx', '.xls', '.csv')):
            raise ValidationError("Please upload an Excel (.xlsx, .xls) or CSV file")
        
        # Read the file
        try:
            import pandas as pd
            if filename.lower().endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
        except Exception as e:
            raise ValidationError(f"Error reading file: {str(e)}")
        
        # Check if file is empty
        if df.empty:
            raise ValidationError("The uploaded file is empty")
        
        # Process the DataFrame using shared logic
        transactions_data, errors = _process_transactions_dataframe(client_id, df, filename)
        
        if errors:
            # Return validation errors instead of throwing exception
            error_msg = "; ".join(errors[:10])
            if len(errors) > 10:
                error_msg += f"... and {len(errors) - 10} more errors"
            
            return APIResponse.error(
                message=f'File validation failed with {len(errors)} errors. Please fix the following issues and try again:',
                details={
                    'file_name': filename,
                    'total_rows': len(df),
                    'validation_errors': errors,
                    'error_count': len(errors),
                    'valid_transactions': len(transactions_data),
                    'processed_count': len(transactions_data),
                    'success': False,
                    'required_columns': ['Date', 'Type', 'Stock', 'Transacted Units', 'Transacted Price (per unit)'],
                    'found_columns': list(df.columns),
                    'total_errors': len(errors)
                }
            )
        
        if not transactions_data:
            return APIResponse.error(
                message='No valid transactions found in file. Please check your data format.',
                details={
                    'file_name': filename,
                    'total_rows': len(df),
                    'validation_errors': ['No valid transactions found'],
                    'error_count': 1,
                    'valid_transactions': 0
                }
            )
        
        # Use bulk create functionality
        bulk_data = {'transactions': transactions_data}
        
        # Process transactions in a database transaction
        try:
            created_transactions = []
            
            # Process each transaction
            for transaction_data in transactions_data:
                # Parse date string (YYYY-MM-DD) and combine with midnight time (IST calendar date with no TZ shift)
                date_str = transaction_data['transaction_date']
                if isinstance(date_str, str):
                    # Parse date string to date object
                    parsed_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                else:
                    # Already a date object
                    parsed_date = date_str if isinstance(date_str, date) else date_str.date()
                transaction_date = datetime.combine(parsed_date, time(0, 0, 0))
                
                # Create transaction
                transaction = Transaction(
                    client_id=client_id,
                    security_id=transaction_data['security_id'],
                    type=transaction_data['type'],
                    quantity=transaction_data['quantity'],
                    price=transaction_data['price'],
                    amount=transaction_data['amount'],
                    transaction_date=transaction_date,
                    created_at=datetime.utcnow()
                )
                
                db.session.add(transaction)
                created_transactions.append(transaction)
            
            # Commit transactions first
            db.session.commit()
            
            # Update holdings and cashflows
            print(f"DEBUG: Updating holdings for client {client_id}")
            holdings_result = update_holdings_from_transactions_api(client_id)
            print(f"DEBUG: Holdings update result: {holdings_result}")
            
            print(f"DEBUG: Updating cashflows for client {client_id}")
            cashflow_result = update_cashflows_from_transactions_api(client_id)
            print(f"DEBUG: Cashflow update result: {cashflow_result}")
            
            # Prepare response data
            response_data = {
                'file_name': filename,
                'total_rows': len(df),
                'processed_count': len(created_transactions),
                'transactions': [{
                    'id': t.id,
                    'security_id': t.security_id,
                    'type': t.type,
                    'quantity': float(t.quantity),
                    'price': float(t.price),
                    'amount': float(t.amount),
                    'transaction_date': t.transaction_date.isoformat(),
                    'notes': None
                } for t in created_transactions],
                'holdings_updated': holdings_result['data'],
                'cashflows_updated': cashflow_result['data']
            }
            
            return APIResponse.success(
                data=response_data,
                message=f"Successfully processed {len(created_transactions)} transactions from file",
                status_code=201
            )
            
        except Exception as e:
            db.session.rollback()
            raise Exception(f"File processing failed: {str(e)}")
        
    except Exception as e:
        logger.error(f"Error uploading transactions file for client {client_id}: {str(e)}")
        raise

# ============================================================================
# HELPER FUNCTIONS - WRAPPER FOR EXISTING FUNCTIONS
# ============================================================================

def update_holdings_from_transactions_api(client_id):
    """
    API-compatible holdings update function
    Returns API-friendly response format
    """
    try:
        # Simple holdings update - just recalculate from transactions
        # This is a simplified version that works in API context
        
        # Get all transactions for the client
        transactions = Transaction.query.filter_by(client_id=client_id).all()
        
        # Clear existing holdings
        Holding.query.filter_by(client_id=client_id).delete()
        
        # Recalculate holdings from transactions
        holdings_dict = {}
        
        for transaction in transactions:
            security_id = transaction.security_id
            
            if security_id not in holdings_dict:
                holdings_dict[security_id] = {
                    'quantity': 0,
                    'total_cost': 0,
                    'transactions': []
                }
            
            if transaction.type == 'BUY':
                holdings_dict[security_id]['quantity'] += float(transaction.quantity)
                holdings_dict[security_id]['total_cost'] += float(transaction.amount)
            elif transaction.type == 'SELL':
                holdings_dict[security_id]['quantity'] -= float(transaction.quantity)
                holdings_dict[security_id]['total_cost'] -= float(transaction.amount)
            elif transaction.type == 'SPLIT':
                # Stock split: quantity increases, average price decreases
                holdings_dict[security_id]['quantity'] *= float(transaction.quantity)
                holdings_dict[security_id]['total_cost'] = holdings_dict[security_id]['total_cost']  # Cost stays same
            elif transaction.type == 'BONUS':
                # Bonus shares: quantity increases, average price decreases
                holdings_dict[security_id]['quantity'] += holdings_dict[security_id]['quantity'] * float(transaction.quantity)
                holdings_dict[security_id]['total_cost'] = holdings_dict[security_id]['total_cost']  # Cost stays same
            
            holdings_dict[security_id]['transactions'].append(transaction)
        
        # Create/update holdings
        for security_id, data in holdings_dict.items():
            if data['quantity'] > 0:  # Only create holdings for positive quantities
                average_price = data['total_cost'] / data['quantity'] if data['quantity'] > 0 else 0
                
                holding = Holding(
                    client_id=client_id,
                    security_id=security_id,
                    quantity=data['quantity'],
                    average_price=average_price
                )
                db.session.add(holding)
        
        db.session.commit()
        
        # Return API-friendly response
        return {
            'success': True,
            'message': 'Holdings updated successfully',
            'data': {
                'client_id': client_id,
                'updated_at': datetime.utcnow().isoformat(),
                'holdings_count': len(holdings_dict),
                'result': 'Holdings recalculated from transactions'
            }
        }
        
    except Exception as e:
        logger.error(f"Error updating holdings for client {client_id}: {str(e)}")
        db.session.rollback()
        return {
            'success': False,
            'message': f'Holdings update failed: {str(e)}',
            'data': None
        }

def update_cashflows_from_transactions_api(client_id):
    """
    API-compatible cashflows update function using CashflowService
    INCREMENTAL UPDATE ONLY - never deletes existing cashflows
    PROTECTS manual uploads (dummy dates) from recalculation
    
    Returns API-friendly response format
    """
    try:
        from services.cashflow_service import CashflowService, DUMMY_DATES
        
        logger.info(f"Starting incremental cashflow update for client {client_id}")
        
        # Get all transactions for the client
        transactions = Transaction.query.filter_by(client_id=client_id).all()
        logger.info(f"Found {len(transactions)} transactions for client {client_id}")
        
        # Get unique dates from transactions
        unique_dates = set()
        for transaction in transactions:
            if transaction.type in ['BUY', 'SELL']:  # Only BUY and SELL affect cashflow
                unique_dates.add(transaction.transaction_date)
        
        # Filter out dummy dates to protect manual uploads
        # Dummy dates like 2000-01-01 indicate manual cashflow uploads
        real_dates = [d for d in unique_dates if d not in DUMMY_DATES]
        dummy_dates_found = [d for d in unique_dates if d in DUMMY_DATES]
        
        if dummy_dates_found:
            logger.info(f"Protected {len(dummy_dates_found)} dummy dates from recalculation (manual uploads)")
        
        logger.info(f"Recalculating cashflows for {len(real_dates)} real transaction dates")
        
        # Use CashflowService to incrementally update cashflows
        # This will NOT delete existing manual cashflows, only update transaction-derived ones
        if real_dates:
            result = CashflowService.recalculate_for_dates(
                client_id=client_id,
                dates=real_dates
            )
            
            logger.info(f"Cashflow service result: {result}")
            
            # Return API-friendly response
            return {
                'success': True,
                'message': 'Cashflows updated incrementally (manual uploads protected)',
                'data': {
                    'client_id': client_id,
                    'updated_at': datetime.utcnow().isoformat(),
                    'total_dates': len(unique_dates),
                    'real_dates_processed': len(real_dates),
                    'dummy_dates_protected': len(dummy_dates_found),
                    'results': result.get('results', []),
                    'result': 'Cashflows incrementally updated via CashflowService'
                }
            }
        else:
            return {
                'success': True,
                'message': 'No cashflow-affecting transactions found',
                'data': {
                    'client_id': client_id,
                    'dates_processed': 0,
                    'result': 'No updates needed'
                }
            }
        
    except Exception as e:
        logger.error(f"Error updating cashflows for client {client_id}: {str(e)}")
        db.session.rollback()
        return {
            'success': False,
            'message': f'Cashflow update failed: {str(e)}',
            'data': None
        }
