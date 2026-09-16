"""
V2 Transaction API
Implements incremental processing for single transactions and forward calculation for bulk operations

Key Features:
- O(1) performance for single transactions
- O(n) accuracy for bulk operations
- Proper corporate action handling
- Zero disruption from V1
"""
from flask import Blueprint, request, jsonify
from datetime import datetime, date, time, timedelta
import logging

from services.transaction_orchestrator import TransactionOrchestrator
from services.price_service import PriceService
from services.transaction_list_service import (
    list_traded_securities,
    parse_v2_list_filters,
    summarize_filtered_query,
)
from extensions import db
from api.v2.transactions_query import build_transactions_v2_query

logger = logging.getLogger(__name__)

# Create blueprint
transactions_v2_bp = Blueprint('transactions_v2', __name__, url_prefix='/api/v2/transactions')


@transactions_v2_bp.before_request
def _v2_transactions_require_auth():
    from api.v2.auth_guard import enforce_v2_api_auth
    return enforce_v2_api_auth()


# Disable CSRF protection for API endpoints
from extensions import csrf


@transactions_v2_bp.route('/<int:client_id>/transactions', methods=['GET'])
def get_client_transactions_v2(client_id):
    """
    V2: Get all transactions for a client with pagination, filtering, and buy/sell summary.
    Filters: transaction_type, security_id, date_from, date_to (YYYY-MM-DD, inclusive).
    Summary is computed on the full filtered set, not only the current page.
    """
    try:
        from models import Client, Transaction, Security
        from access_control import can_access_client
        
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        if not can_access_client(client_id):
            return jsonify({
                'success': False,
                'message': 'Access denied',
            }), 403
        
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        filters, filter_error = parse_v2_list_filters(request.args)
        if filter_error:
            return jsonify({
                'success': False,
                'message': filter_error,
            }), 400
        
        # Build canonical V2 query (shared logic). Summary uses a separate
        # query so with_entities cannot affect pagination.
        summary_query = build_transactions_v2_query(client_id=client_id, **filters)
        summary = summarize_filtered_query(summary_query)

        query = build_transactions_v2_query(client_id=client_id, **filters)
        
        # Eager load security and asset_class relationships to avoid N+1 queries
        from sqlalchemy.orm import joinedload
        query = query.options(
            joinedload(Transaction.security).joinedload(Security.asset_class),
            joinedload(Transaction.client)
        )
        
        # Order by transaction date (newest first)
        query = query.order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
        
        # Paginate
        pagination = query.paginate(
            page=page, 
            per_page=per_page, 
            error_out=False
        )
        
        # Format response
        transactions = []
        for transaction in pagination.items:
            # Get asset class from security with proper error handling
            asset_class_name = 'Unknown'
            try:
                if transaction.security:
                    # Try to get asset class from relationship (eager loaded)
                    if hasattr(transaction.security, 'asset_class') and transaction.security.asset_class:
                        asset_class_name = transaction.security.asset_class.name or 'Unknown'
                    # Fallback: try to get from asset_class_id if relationship not loaded or is None
                    if asset_class_name == 'Unknown' and hasattr(transaction.security, 'asset_class_id') and transaction.security.asset_class_id:
                        from models import AssetClass
                        asset_class = AssetClass.query.get(transaction.security.asset_class_id)
                        if asset_class:
                            asset_class_name = asset_class.name or 'Unknown'
            except Exception as e:
                logger.warning(f"Error getting asset class for transaction {transaction.id}: {str(e)}")
                asset_class_name = 'Unknown'
            
            transactions.append({
                'id': transaction.id,
                'client_id': transaction.client_id,
                'client_name': getattr(transaction.client, 'name', 'Unknown') if transaction.client else 'Unknown',
                'security_id': transaction.security_id,
                'security_symbol': getattr(transaction.security, 'symbol', 'Unknown') if transaction.security else 'Unknown',
                'security_name': getattr(transaction.security, 'name', 'Unknown') if transaction.security else 'Unknown',
                'asset_class': asset_class_name,
                'type': transaction.type,
                'quantity': float(transaction.quantity) if transaction.quantity else 0,
                'price': float(transaction.price) if transaction.price else 0,
                'transaction_date': transaction.transaction_date.isoformat() if transaction.transaction_date else None,
                'created_at': transaction.created_at.isoformat() if transaction.created_at else None,
                'notes': getattr(transaction, 'notes', None)
            })
        
        return jsonify({
            'success': True,
            'data': {
                'transactions': transactions,
                'summary': summary,
                'pagination': {
                    'page': pagination.page,
                    'per_page': pagination.per_page,
                    'total': pagination.total,
                    'pages': pagination.pages,
                    'has_next': pagination.has_next,
                    'has_prev': pagination.has_prev
                }
            }
        })
        
    except Exception as e:
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            raise
        logger.error(f"Error getting V2 transactions for client {client_id}: {str(e)}")
        return jsonify({
            'success': False,
            'message': "Failed to retrieve transactions"
        }), 500


@transactions_v2_bp.route('/<int:client_id>/traded-securities', methods=['GET'])
def get_client_traded_securities_v2(client_id):
    """Distinct securities the client has traded, for the Transactions stock filter."""
    try:
        from models import Client
        from access_control import can_access_client

        Client.query.get_or_404(client_id)
        if not can_access_client(client_id):
            return jsonify({
                'success': False,
                'message': 'Access denied',
            }), 403

        securities = list_traded_securities(client_id)
        return jsonify({
            'success': True,
            'data': {
                'securities': list(securities),
            },
        })
    except Exception as e:
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            raise
        logger.error(f"Error listing traded securities for client {client_id}: {str(e)}")
        return jsonify({
            'success': False,
            'message': "Failed to retrieve securities",
        }), 500


@transactions_v2_bp.route('/<int:client_id>/transactions', methods=['POST'])
def create_transaction_v2(client_id):
    """
    V2: Create single transaction with incremental processing
    
    Request Body:
    {
        "security_id": 123,
        "type": "BUY",
        "quantity": 100,
        "price": 1500.0,
        "transaction_date": "2024-01-15"
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'message': 'No data provided'
            }), 400
        
        # Add client_id to transaction data
        data['client_id'] = client_id
        
        # Validate required fields
        required_fields = ['security_id', 'type', 'quantity', 'price']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'message': f'Missing required field: {field}'
                }), 400
        
        # Process transaction using orchestrator
        orchestrator = TransactionOrchestrator(db.session)
        result = orchestrator.create_single_transaction(data)
        
        if result['success']:
            db.session.commit()
            return jsonify({
                'success': True,
                'data': {'transaction_id': result['transaction_id']},
                'message': (
                    "Transaction created successfully (V2). "
                    "Holdings and cashflow were updated."
                ),
            }), 201
        else:
            db.session.rollback()
            return jsonify({
                'success': False,
                'message': result['message']
            }), 400
            
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating V2 transaction: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"V2 transaction creation failed: {str(e)}"
        }), 500


@transactions_v2_bp.route('/<int:client_id>/transactions/<int:transaction_id>', methods=['PUT'])
def update_transaction_v2(client_id, transaction_id):
    """
    V2: Update single transaction with incremental processing
    
    Request Body:
    {
        "quantity": 150,
        "price": 1600.0
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'message': 'No data provided'
            }), 400
        
        # Process transaction update using orchestrator
        orchestrator = TransactionOrchestrator(db.session)
        result = orchestrator.update_single_transaction(transaction_id, data)
        
        if result['success']:
            db.session.commit()
            return jsonify({
                'success': True,
                'message': (
                    "Transaction updated successfully (V2). "
                    "Cashflows were not changed — edit the matching cashflow row if needed."
                ),
                'cashflow_manual_review': True,
                'requires_acknowledgment': True,
                'cashflow_review_guide_url': '/help/trade-edit-cashflow-review',
            })
        else:
            db.session.rollback()
            return jsonify({
                'success': False,
                'message': result['message']
            }), 400
            
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating V2 transaction: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"V2 transaction update failed: {str(e)}"
        }), 500


@transactions_v2_bp.route('/<int:client_id>/transactions/<int:transaction_id>', methods=['DELETE'])
def delete_transaction_v2(client_id, transaction_id):
    """
    V2: Delete single transaction with incremental processing
    """
    try:
        # Process transaction deletion using orchestrator
        orchestrator = TransactionOrchestrator(db.session)
        result = orchestrator.delete_single_transaction(transaction_id)
        
        if result['success']:
            db.session.commit()
            return jsonify({
                'success': True,
                'message': (
                    "Transaction deleted successfully (V2). "
                    "Cashflows were not changed — add an offsetting cashflow manually if needed."
                ),
                'cashflow_manual_review': True,
                'cashflow_review_guide_url': '/help/trade-edit-cashflow-review',
            })
        else:
            db.session.rollback()
            return jsonify({
                'success': False,
                'message': result['message']
            }), 400
            
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting V2 transaction: {str(e)}")
        import traceback
        return jsonify({
            'success': False,
            'message': f"V2 transaction deletion failed: {str(e)}",
            'details': traceback.format_exc()
        }), 500


@transactions_v2_bp.route('/<int:client_id>/transactions/bulk-delete', methods=['POST'])
def bulk_delete_transactions_v2(client_id):
    """
    V2: Bulk delete transactions using orchestrator
    """
    try:
        data = request.get_json()
        
        if not data or 'transaction_ids' not in data:
            return jsonify({
                'success': False,
                'message': 'No transaction IDs provided'
            }), 400
        
        transaction_ids = data['transaction_ids']
        if not transaction_ids:
            return jsonify({
                'success': False,
                'message': 'No transactions selected for deletion'
            }), 400
        
        # Process bulk deletion using orchestrator (restrict to client)
        orchestrator = TransactionOrchestrator(db.session)
        result = orchestrator.bulk_delete_transactions(transaction_ids, client_id=client_id)
        
        if result['success']:
            db.session.commit()
            return jsonify({
                'success': True,
                'message': f"Successfully deleted {result['deleted_count']} transactions (V2)",
                'data': {
                    'deleted_count': result['deleted_count'],
                    'not_found_ids': result.get('not_found_ids', [])
                }
            })
        else:
            db.session.rollback()
            return jsonify({
                'success': False,
                'message': result['message']
            }), 400
            
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in V2 bulk delete: {str(e)}")
        import traceback
        return jsonify({
            'success': False,
            'message': f"V2 bulk delete failed: {str(e)}",
            'details': traceback.format_exc()
        }), 500


@transactions_v2_bp.route('/<int:client_id>/transactions/bulk', methods=['POST'])
def create_bulk_transactions_v2(client_id):
    """
    V2: Create bulk transactions with forward calculation
    
    Request Body:
    {
        "transactions": [
            {
                "security_id": 123,
                "type": "BUY",
                "quantity": 100,
                "price": 1500.0,
                "transaction_date": "2024-01-15"
            },
            ...
        ]
    }
    """
    try:
        data = request.get_json()
        
        if not data or 'transactions' not in data:
            return jsonify({
                'success': False,
                'message': 'No transactions array provided'
            }), 400
        
        transactions_data = data['transactions']
        
        if not transactions_data:
            return jsonify({
                'success': False,
                'message': 'Transactions array is empty'
            }), 400
        
        # Add client_id to all transactions
        for transaction_data in transactions_data:
            transaction_data['client_id'] = client_id
        
        # Process bulk transactions using orchestrator
        orchestrator = TransactionOrchestrator(db.session)
        result = orchestrator.process_bulk_transactions(transactions_data)
        
        if result['success']:
            db.session.commit()
            return jsonify({
                'success': True,
                'data': {'created_count': result['created_count']},
                'message': f"Successfully created {result['created_count']} transactions (V2)"
            }), 201
        else:
            db.session.rollback()
            return jsonify({
                'success': False,
                'message': result['message']
            }), 400
            
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating V2 bulk transactions: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"V2 bulk transaction creation failed: {str(e)}"
        }), 500


@transactions_v2_bp.route('/<int:client_id>/transactions/refresh', methods=['POST'])
def refresh_holdings_v2(client_id):
    """
    V2: Data correction using forward calculation
    Recalculates all holdings for a client using forward calculation
    """
    try:
        # Process holdings refresh using orchestrator
        orchestrator = TransactionOrchestrator(db.session)
        result = orchestrator.refresh_all_holdings(client_id)
        
        if result['success']:
            db.session.commit()
            return jsonify({
                'success': True,
                'message': "Holdings refreshed successfully (V2)"
            })
        else:
            db.session.rollback()
            return jsonify({
                'success': False,
                'message': result['message']
            }), 400
            
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error refreshing V2 holdings: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"V2 holdings refresh failed: {str(e)}"
        }), 500


@transactions_v2_bp.route('/health', methods=['GET'])
def health_check():
    """
    Health check endpoint for V2 transaction API
    """
    return jsonify({
        'status': 'healthy',
        'service': 'transactions_v2',
        'version': '2.0.0',
        'features': [
            'Incremental single transaction processing (O(1))',
            'Forward calculation bulk processing (O(n))',
            'Proper corporate action handling',
            'Zero disruption from V1'
        ]
    })


@transactions_v2_bp.route('/upload', methods=['POST'])
def upload_transactions_file():
    """
    V2: Upload transactions from Excel/CSV file
    Uses V2 bulk processing with forward calculation
    """
    try:
        from werkzeug.utils import secure_filename
        import pandas as pd
        import re
        
        # Get client_id from form
        client_id = request.form.get('client_id')
        if not client_id:
            return jsonify({
                'success': False,
                'message': '❌ Client ID is required. Please select a client before uploading.',
                'error_type': 'missing_client'
            }), 400
        
        try:
            client_id = int(client_id)
        except ValueError:
            return jsonify({
                'success': False,
                'message': '❌ Invalid Client ID format.',
                'error_type': 'invalid_client_id'
            }), 400
        
        # Check if file is present
        if 'file' not in request.files:
            return jsonify({
                'success': False,
                'message': '❌ No file provided. Please select a file to upload.',
                'error_type': 'no_file'
            }), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({
                'success': False,
                'message': '❌ No file selected. Please choose a file to upload.',
                'error_type': 'empty_filename'
            }), 400
        
        # Check file extension
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(('.xlsx', '.xls', '.csv')):
            return jsonify({
                'success': False,
                'message': '❌ Invalid file type. Please upload an Excel (.xlsx, .xls) or CSV file only.',
                'error_type': 'invalid_file_type',
                'supported_formats': ['.xlsx', '.xls', '.csv']
            }), 400
        
        # Read the file
        try:
            if filename.lower().endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'❌ Error reading file: {str(e)}. Please check if the file is corrupted or in the correct format.',
                'error_type': 'file_read_error'
            }), 400
        
        # Check if file is empty
        if df.empty:
            return jsonify({
                'success': False,
                'message': '❌ The uploaded file is empty. Please add transaction data to the file.',
                'error_type': 'empty_file'
            }), 400
        
        # Validate required columns with detailed feedback
        required_columns = ['Date', 'Type', 'Stock', 'Transacted Units', 'Transacted Price (per unit)']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            return jsonify({
                'success': False,
                'message': f'❌ Missing required columns: {", ".join(missing_columns)}. Please ensure your file has these exact column names.',
                'error_type': 'missing_columns',
                'required_columns': required_columns,
                'missing_columns': missing_columns,
                'found_columns': list(df.columns)
            }), 400
        
        # Parse transactions from file with detailed error reporting
        transactions_data = []
        errors = []
        problematic_rows = []
        
        for index, row in df.iterrows():
            row_errors = []
            row_data = {
                'row_number': index + 2,
                'date': row.get('Date', ''),
                'type': row.get('Type', ''),
                'stock': row.get('Stock', ''),
                'quantity': row.get('Transacted Units', ''),
                'price': row.get('Transacted Price (per unit)', ''),
                'errors': []
            }
            
            try:
                # Parse date
                if pd.isna(row['Date']):
                    row_errors.append('Date is missing or empty')
                else:
                    try:
                        # Use safe date parsing to avoid timezone conversion issues
                        from utils.date_utils import parse_date_safe
                        transaction_date = parse_date_safe(row['Date'])
                        
                        # PROTECTION: Reject dummy dates in V2 upload
                        from services.cashflow_service import DUMMY_DATES
                        if transaction_date in DUMMY_DATES:
                            row_errors.append(f'Date {transaction_date} is a dummy date - use real transaction dates only')
                    except Exception as e:
                        row_errors.append(f'Invalid date format "{row["Date"]}". Expected formats: DD-MMM-YYYY, DD/MM/YYYY, YYYY-MM-DD, DD-MM-YYYY, MM/DD/YYYY')
                
                # Parse transaction type
                if pd.isna(row['Type']):
                    row_errors.append('Transaction type is missing or empty')
                else:
                    transaction_type = str(row['Type']).upper().strip()
                    if transaction_type not in ['BUY', 'SELL']:
                        row_errors.append(f'Invalid transaction type "{transaction_type}". V2 only supports BUY and SELL transactions')
                
                # Parse security
                if pd.isna(row['Stock']):
                    row_errors.append('Stock symbol is missing or empty')
                else:
                    security_symbol = str(row['Stock']).strip()
                    
                    # Strip exchange prefixes
                    clean_symbol = security_symbol
                    if security_symbol.startswith(('NSE:', 'BSE:', 'BOM:')):
                        clean_symbol = security_symbol.split(':', 1)[1]
                    
                    # Find security
                    from models import Security
                    security = Security.query.filter_by(symbol=clean_symbol).first()
                    if not security:
                        security = Security.query.filter_by(name=security_symbol).first()
                    
                    if not security:
                        row_errors.append(f'Security "{security_symbol}" not found in database. Please check the symbol or add the security first.')
                
                # Parse quantity and price
                try:
                    if pd.isna(row['Transacted Units']):
                        row_errors.append('Quantity is missing or empty')
                    else:
                        quantity = float(row['Transacted Units'])
                        if quantity <= 0:
                            row_errors.append(f'Quantity must be positive (found: {row.get("Transacted Units", "N/A")})')
                except (ValueError, TypeError):
                    row_errors.append(f'Invalid quantity format - Quantity: "{row.get("Transacted Units", "N/A")}". Expected numeric value.')
                
                try:
                    if pd.isna(row['Transacted Price (per unit)']):
                        row_errors.append('Price is missing or empty')
                    else:
                        price_str = str(row['Transacted Price (per unit)'])
                        price_str = re.sub(r'[₹$,]', '', price_str).strip()
                        price = float(price_str)
                        if price <= 0:
                            row_errors.append(f'Price must be positive (found: {row.get("Transacted Price (per unit)", "N/A")})')
                except (ValueError, TypeError):
                    row_errors.append(f'Invalid price format - Price: "{row.get("Transacted Price (per unit)", "N/A")}". Expected numeric value.')
                
                # Add row errors to main errors list
                if row_errors:
                    row_data['errors'] = row_errors
                    problematic_rows.append(row_data)
                    errors.append(f'Row {index + 2}: {"; ".join(row_errors)}')
                else:
                    # All validations passed, add transaction
                    transactions_data.append({
                        'security_id': security.id,
                        'type': transaction_type,
                        'quantity': quantity,
                        'price': price,
                        'transaction_date': transaction_date
                    })
                
            except Exception as e:
                errors.append(f'Row {index + 2}: Unexpected error - {str(e)}')
                continue
        
        # Return preview data for review (same as V1 approach)
        if errors:
            return jsonify({
                'success': True,
                'message': f'Preview generated. {len(transactions_data)} valid transactions found.',
                'data': {
                    'file_name': filename,
                    'total_rows': len(df),
                    'valid_transactions': transactions_data,
                    'problematic_rows': problematic_rows,
                    'errors': errors,
                    'error_count': len(errors),
                    'required_columns': ['Date', 'Type', 'Stock', 'Transacted Units', 'Transacted Price (per unit)'],
                    'found_columns': list(df.columns)
                }
            }), 200
        
        # Check if we have any valid transactions
        if not transactions_data:
            return jsonify({
                'success': False,
                'message': '❌ No valid transactions found in file. Please check your data format.',
                'error_type': 'no_valid_transactions'
            }), 400
        
        # Use V2 bulk processing
        orchestrator = TransactionOrchestrator(db.session)
        result = orchestrator.process_bulk_transactions({
            'client_id': client_id,
            'transactions': transactions_data
        })
        
        if result['success']:
            db.session.commit()
            return jsonify({
                'success': True,
                'message': f'✅ Successfully uploaded {len(transactions_data)} transactions using V2 processing',
                'data': {
                    'processed_count': len(transactions_data),
                    'error_count': len(errors)
                }
            }), 201
        else:
            db.session.rollback()
            return jsonify({
                'success': False,
                'message': f'❌ Processing failed: {result.get("message", "Unknown error")}',
                'error_type': 'processing_error'
            }), 500
        
    except Exception as e:
        logger.error(f"V2 file upload failed: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'❌ Upload failed due to server error: {str(e)}. Please try again or contact support.',
            'error_type': 'server_error'
        }), 500


@transactions_v2_bp.route('/<int:client_id>/preview', methods=['POST'])
def preview_transactions_file_v2(client_id):
    """
    V2: Preview transactions from uploaded file before processing
    Uses the same approach as V1 preview
    """
    try:
        from werkzeug.utils import secure_filename
        import pandas as pd
        import re
        
        # Get client_id from form
        client_id = request.form.get('client_id')
        if not client_id:
            return jsonify({
                'success': False,
                'message': '❌ Client ID is required. Please select a client before uploading.',
                'error_type': 'missing_client'
            }), 400
        
        try:
            client_id = int(client_id)
        except ValueError:
            return jsonify({
                'success': False,
                'message': '❌ Invalid Client ID format.',
                'error_type': 'invalid_client_id'
            }), 400
        
        # Check if file is present
        if 'file' not in request.files:
            return jsonify({
                'success': False,
                'message': '❌ No file provided. Please select a file to upload.',
                'error_type': 'no_file'
            }), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({
                'success': False,
                'message': '❌ No file selected. Please choose a file to upload.',
                'error_type': 'empty_filename'
            }), 400
        
        # Check file extension
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(('.xlsx', '.xls', '.csv')):
            return jsonify({
                'success': False,
                'message': '❌ Invalid file type. Please upload an Excel (.xlsx, .xls) or CSV file only.',
                'error_type': 'invalid_file_type',
                'supported_formats': ['.xlsx', '.xls', '.csv']
            }), 400
        
        # Read the file
        try:
            if filename.lower().endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'❌ Error reading file: {str(e)}. Please check if the file is corrupted or in the correct format.',
                'error_type': 'file_read_error'
            }), 400
        
        # Check if file is empty
        if df.empty:
            return jsonify({
                'success': False,
                'message': '❌ The uploaded file is empty. Please add transaction data to the file.',
                'error_type': 'empty_file'
            }), 400
        
        # Validate required columns with detailed feedback
        required_columns = ['Date', 'Type', 'Stock', 'Transacted Units', 'Transacted Price (per unit)']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            return jsonify({
                'success': False,
                'message': f'❌ Missing required columns: {", ".join(missing_columns)}. Please ensure your file has these exact column names.',
                'error_type': 'missing_columns',
                'required_columns': required_columns,
                'missing_columns': missing_columns,
                'found_columns': list(df.columns)
            }), 400
        
        # Parse transactions from file with detailed error reporting
        transactions_data = []
        errors = []
        problematic_rows = []
        
        for index, row in df.iterrows():
            row_errors = []
            row_data = {
                'row_number': index + 2,
                'date': row.get('Date', ''),
                'type': row.get('Type', ''),
                'stock': row.get('Stock', ''),
                'quantity': row.get('Transacted Units', ''),
                'price': row.get('Transacted Price (per unit)', ''),
                'errors': []
            }
            
            try:
                # Parse date
                if pd.isna(row['Date']):
                    row_errors.append('Date is missing or empty')
                else:
                    try:
                        # Use safe date parsing to avoid timezone conversion issues
                        from utils.date_utils import parse_date_safe
                        transaction_date = parse_date_safe(row['Date'])
                        
                        # PROTECTION: Reject dummy dates in V2 upload
                        from services.cashflow_service import DUMMY_DATES
                        if transaction_date in DUMMY_DATES:
                            row_errors.append(f'Date {transaction_date} is a dummy date - use real transaction dates only')
                    except Exception as e:
                        row_errors.append(f'Invalid date format "{row["Date"]}". Expected formats: DD-MMM-YYYY, DD/MM/YYYY, YYYY-MM-DD, DD-MM-YYYY, MM/DD/YYYY')
                
                # Parse transaction type
                if pd.isna(row['Type']):
                    row_errors.append('Transaction type is missing or empty')
                else:
                    transaction_type = str(row['Type']).upper().strip()
                    if transaction_type not in ['BUY', 'SELL']:
                        row_errors.append(f'Invalid transaction type "{transaction_type}". V2 only supports BUY and SELL transactions')
                
                # Parse security
                if pd.isna(row['Stock']):
                    row_errors.append('Stock symbol is missing or empty')
                else:
                    security_symbol = str(row['Stock']).strip()
                    
                    # Strip exchange prefixes
                    clean_symbol = security_symbol
                    if security_symbol.startswith(('NSE:', 'BSE:', 'BOM:')):
                        clean_symbol = security_symbol.split(':', 1)[1]
                    
                    # Find security
                    from models import Security
                    security = Security.query.filter_by(symbol=clean_symbol).first()
                    if not security:
                        security = Security.query.filter_by(name=security_symbol).first()
                    
                    if not security:
                        row_errors.append(f'Security "{security_symbol}" not found in database. Please check the symbol or add the security first.')
                
                # Parse quantity and price
                try:
                    if pd.isna(row['Transacted Units']):
                        row_errors.append('Quantity is missing or empty')
                    else:
                        quantity = float(row['Transacted Units'])
                        if quantity <= 0:
                            row_errors.append(f'Quantity must be positive (found: {row.get("Transacted Units", "N/A")})')
                except (ValueError, TypeError):
                    row_errors.append(f'Invalid quantity format - Quantity: "{row.get("Transacted Units", "N/A")}". Expected numeric value.')
                
                try:
                    if pd.isna(row['Transacted Price (per unit)']):
                        row_errors.append('Price is missing or empty')
                    else:
                        price_str = str(row['Transacted Price (per unit)'])
                        price_str = re.sub(r'[₹$,]', '', price_str).strip()
                        price = float(price_str)
                        if price <= 0:
                            row_errors.append(f'Price must be positive (found: {row.get("Transacted Price (per unit)", "N/A")})')
                except (ValueError, TypeError):
                    row_errors.append(f'Invalid price format - Price: "{row.get("Transacted Price (per unit)", "N/A")}". Expected numeric value.')
                
                # Add row errors to main errors list
                if row_errors:
                    row_data['errors'] = row_errors
                    problematic_rows.append(row_data)
                    errors.append(f'Row {index + 2}: {"; ".join(row_errors)}')
                else:
                    # All validations passed, add transaction
                    transactions_data.append({
                        'security_id': security.id,
                        'type': transaction_type,
                        'quantity': quantity,
                        'price': price,
                        'transaction_date': transaction_date
                    })
                
            except Exception as e:
                errors.append(f'Row {index + 2}: Unexpected error - {str(e)}')
                continue
        
        # Return preview data (same as V1 approach)
        return jsonify({
            'success': True,
            'message': f'Preview generated. {len(transactions_data)} valid transactions found.',
            'data': {
                'file_name': filename,
                'total_rows': len(df),
                'valid_transactions': transactions_data,
                'problematic_rows': problematic_rows,
                'errors': errors,
                'error_count': len(errors),
                'required_columns': ['Date', 'Type', 'Stock', 'Transacted Units', 'Transacted Price (per unit)'],
                'found_columns': list(df.columns)
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Error in V2 preview: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error previewing transactions: {str(e)}',
            'error_type': 'preview_error'
        }), 500


@transactions_v2_bp.route('/<int:client_id>/process', methods=['POST'])
def process_selected_transactions_v2(client_id):
    """
    V2: Process selected transactions after review
    """
    try:
        data = request.get_json()
        
        if not data or 'transactions' not in data:
            return jsonify({
                'success': False,
                'message': 'No transactions provided for processing',
                'error_type': 'missing_transactions'
            }), 400
        
        selected_transactions = data['transactions']
        if not selected_transactions:
            return jsonify({
                'success': False,
                'message': 'No transactions selected for processing',
                'error_type': 'empty_selection'
            }), 400
        
        # Use V2 bulk processing
        orchestrator = TransactionOrchestrator()
        result = orchestrator.process_bulk_transactions(client_id, selected_transactions)
        
        if result['success']:
            return jsonify({
                'success': True,
                'message': f'Successfully processed {len(selected_transactions)} transactions using V2 processing',
                'data': {
                    'processed_count': len(selected_transactions),
                    'transactions': selected_transactions
                }
            }), 200
        else:
            return jsonify({
                'success': False,
                'message': f'Error processing transactions: {result.get("error", "Unknown error")}',
                'error_type': 'processing_error'
            }), 500
            
    except Exception as e:
        logger.error(f"Error in V2 process selected transactions: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error processing selected transactions: {str(e)}',
            'error_type': 'processing_error'
        }), 500


@transactions_v2_bp.route('/<int:client_id>/holdings', methods=['GET'])
def get_client_holdings_v2(client_id):
    """
    V2: Get client holdings from holdings table
    Simple read with basic calculations
    """
    try:
        from models import Client, Holding, Security
        
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        # Allow larger pages for accurate frontend aggregation (capped to 1000)
        per_page = min(request.args.get('per_page', 50, type=int), 1000)
        include_zero = request.args.get('include_zero', 'false').lower() == 'true'
        sort_by = request.args.get('sort_by', 'current_value')
        sort_order = request.args.get('sort_order', 'desc')
        
        # Get holdings from table
        query = Holding.query.filter_by(client_id=client_id)
        
        # Filter out zero holdings if requested
        if not include_zero:
            query = query.filter(Holding.quantity > 0)
        
        # Get paginated results (for table rows)
        holdings = query.paginate(
            page=page, 
            per_page=per_page, 
            error_out=False
        )
        
        # Prepare response data
        holdings_data = []
        total_value = 0.0
        total_cost = 0.0
        total_unrealized_pnl = 0.0
        
        # Asset class derivation (read-only; shared with client details / recommendations)
        from utils.security_asset_class import derive_asset_class as _derive_asset_class
        from services.holding_advisory_scope_service import (
            SCOPE_BILLING,
            get_excluded_security_ids,
        )

        excluded_ids = get_excluded_security_ids(client_id, SCOPE_BILLING)

        for holding in holdings.items:
            security = Security.query.get(holding.security_id)
            if security:
                # Calculate current_value from up-to-date price (prefer PriceService)
                quantity = float(holding.quantity) if holding.quantity else 0
                try:
                    pd = PriceService.get_price(security.id)
                    # Prefer live price; fall back to stored current_price if live not available
                    if getattr(pd, 'is_valid', False) and pd.price is not None:
                        current_price = float(pd.price)
                    else:
                        current_price = float(security.current_price) if getattr(security, 'current_price', None) else 0.0
                except Exception:
                    current_price = float(security.current_price) if getattr(security, 'current_price', None) else 0.0
                current_value = quantity * current_price
                
                # Calculate total cost from quantity * average_price
                average_price = float(holding.average_price) if holding.average_price else 0
                total_cost_holding = quantity * average_price
                
                # Calculate unrealized P&L
                unrealized_pnl = current_value - total_cost_holding
                unrealized_pnl_percent = (unrealized_pnl / total_cost_holding * 100) if total_cost_holding > 0 else 0
                
                # Determine asset class robustly (Debt ETFs → Debt)
                ac_name = _derive_asset_class(security)

                holding_data = {
                    'id': holding.id,
                    'client_id': holding.client_id,
                    'security_id': holding.security_id,
                    'security_name': security.name,
                    'security_symbol': security.symbol,
                    'security_type': getattr(security, 'security_type', None) or '',
                    'asset_class': ac_name,
                    'quantity': quantity,
                    'average_price': average_price,
                    'current_price': current_price,
                    'total_cost': total_cost_holding,
                    'current_value': current_value,
                    'unrealized_pnl': unrealized_pnl,
                    'unrealized_pnl_percent': unrealized_pnl_percent,
                    'excluded_from_advisory_aua': holding.security_id in excluded_ids,
                    'last_updated': holding.updated_at.isoformat() if holding.updated_at else None
                }
                holdings_data.append(holding_data)
                total_value += current_value
                total_cost += total_cost_holding
                total_unrealized_pnl += unrealized_pnl
        
        # Sort by current_value if requested
        if sort_by == 'current_value':
            holdings_data.sort(key=lambda x: x['current_value'], reverse=(sort_order == 'desc'))
        
        # Calculate summary ACROSS ALL HOLDINGS (not just current page)
        all_total_value = 0.0
        all_total_cost = 0.0
        all_total_unrealized_pnl = 0.0
        all_advisory_aua_value = 0.0
        all_holdings = query.all()
        for h in all_holdings:
            sec = Security.query.get(h.security_id)
            if not sec:
                continue
            qty = float(h.quantity) if h.quantity else 0.0
            try:
                pd = PriceService.get_price(sec.id)
                if getattr(pd, 'is_valid', False) and pd.price is not None:
                    cp = float(pd.price)
                else:
                    cp = float(sec.current_price) if getattr(sec, 'current_price', None) else 0.0
            except Exception:
                cp = float(sec.current_price) if getattr(sec, 'current_price', None) else 0.0
            cv = qty * cp
            ap = float(h.average_price) if h.average_price else 0.0
            tc = qty * ap
            pnl = cv - tc
            all_total_value += cv
            all_total_cost += tc
            all_total_unrealized_pnl += pnl
            if h.security_id not in excluded_ids:
                all_advisory_aua_value += cv
        
        summary = {
            'total_holdings': len(all_holdings),
            'total_value': all_total_value,
            'advisory_aua_value': all_advisory_aua_value,
            'excluded_advisory_aua_value': all_total_value - all_advisory_aua_value,
            'total_cost': all_total_cost,
            'total_unrealized_pnl': all_total_unrealized_pnl,
            'total_unrealized_pnl_percent': (all_total_unrealized_pnl / all_total_cost * 100) if all_total_cost > 0 else 0,
            'page': page,
            'per_page': per_page,
            'pages': holdings.pages
        }
        
        response_data = {
            'holdings': holdings_data,
            'summary': summary
        }
        
        return jsonify({
            'success': True,
            'data': response_data,
            'message': f'Retrieved {len(holdings_data)} holdings for client {client_id}'
        })
        
    except Exception as e:
        logger.error(f"Error getting V2 holdings for client {client_id}: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Failed to get holdings: {str(e)}'
        }), 500


# CSRF exempt allowlist: transactions_v2_bp — /api/v2/* JWT or session via auth_guard
csrf.exempt(transactions_v2_bp)

def _recent_investments_for_summary(client_id: int, top_n: int = 10,
                                    start_date=None, end_date=None):
    """
    Securities with BUY transactions in the given window, grouped and enriched.

    start_date / end_date: date or datetime objects (inclusive).
    Defaults to last 183 days when both are None.
    """
    from models import Transaction, Holding, Security

    today = date.today()
    if start_date is None and end_date is None:
        cutoff_dt = datetime.combine(today - timedelta(days=183), time.min)
        end_dt = datetime.combine(today, time.max)
    else:
        cutoff_dt = datetime.combine(start_date, time.min) if isinstance(start_date, date) else start_date
        end_dt = datetime.combine(end_date, time.max) if isinstance(end_date, date) else end_date
    txs = (
        Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.type == 'BUY',
            Transaction.transaction_date >= cutoff_dt,
            Transaction.transaction_date <= end_dt,
        )
        .order_by(Transaction.transaction_date.asc())
        .all()
    )

    # Group by security_id
    grouped: dict = {}
    for t in txs:
        sid = t.security_id
        if sid not in grouped:
            grouped[sid] = {
                'security_id': sid,
                'security': t.security,
                'invested_amount': 0.0,
                'quantity_bought': 0.0,
                'first_date': t.transaction_date,
                'last_date': t.transaction_date,
            }
        g = grouped[sid]
        g['invested_amount'] += float(t.amount) if t.amount is not None else 0.0
        g['quantity_bought'] += float(t.quantity) if t.quantity is not None else 0.0
        if t.transaction_date and t.transaction_date < g['first_date']:
            g['first_date'] = t.transaction_date
        if t.transaction_date and t.transaction_date > g['last_date']:
            g['last_date'] = t.transaction_date

    total_6m_invested = sum(g['invested_amount'] for g in grouped.values())

    # Sort by invested_amount descending, take top_n
    top_groups = sorted(grouped.values(), key=lambda g: g['invested_amount'], reverse=True)[:top_n]

    rows = []
    for g in top_groups:
        sec = g['security']
        invested = g['invested_amount']       # total cost of 6m BUYs
        qty_bought = g['quantity_bought']     # units bought in 6m

        # Live price — used to value the 6m units specifically
        current_price = 0.0
        if sec:
            try:
                pd = PriceService.get_price(sec.id)
                current_price = float(pd.price) if getattr(pd, 'is_valid', False) and pd.price is not None else (
                    float(sec.current_price) if sec.current_price else 0.0
                )
            except Exception:
                current_price = float(sec.current_price) if sec and sec.current_price else 0.0

        # P&L is purely on the 6m-bought units:
        #   avg_buy_price_6m = invested / qty_bought
        #   current_value_of_6m_units = qty_bought × current_price
        current_value_6m_units = qty_bought * current_price
        pnl_amount = current_value_6m_units - invested
        pnl_percent = (pnl_amount / invested * 100) if invested > 0 else 0.0
        pct_of_6m = (invested / total_6m_invested * 100) if total_6m_invested > 0 else 0.0

        avg_buy_price = (invested / qty_bought) if qty_bought > 0 else 0.0

        rows.append(
            {
                'symbol': sec.symbol if sec else '?',
                'security_name': sec.name if sec else '?',
                'invested_amount': round(invested, 0),
                'current_value': round(current_value_6m_units, 0),
                'avg_buy_price': round(avg_buy_price, 2),
                'current_price': round(current_price, 2),
                'qty_bought': round(qty_bought, 4),
                'pnl_amount': round(pnl_amount, 0),
                'pnl_percent': round(pnl_percent, 2),
                'pct_of_6m_investment': round(pct_of_6m, 1),
                'first_date': g['first_date'].strftime('%b %Y') if g['first_date'] else '',
                'last_date': g['last_date'].strftime('%b %Y') if g['last_date'] else '',
            }
        )
    return rows, round(total_6m_invested, 0)


def _security_sector(security):
    if not security:
        return 'Unknown'
    import json

    # Try meta_data first
    if security.meta_data:
        md = security.meta_data
        try:
            if isinstance(md, dict):
                d = md
            else:
                d = json.loads(md) if isinstance(md, str) else {}
            sector = (str(d.get('sector', '') or d.get('industry', '') or '')).strip()
            if sector:
                return sector
        except Exception:
            pass

    # Fall back to asset class name
    try:
        if security.asset_class and security.asset_class.name:
            return security.asset_class.name
    except Exception:
        pass

    return 'Unknown'


@transactions_v2_bp.route('/<int:client_id>/holdings/summary', methods=['GET'])
def get_client_holdings_summary_v2(client_id):
    """
    V2: Get holdings summary with aggregations
    Top holdings, wealth creators, wealth destructors, smallest holdings
    """
    try:
        from models import Client, Holding, Security

        # Verify client exists
        client = Client.query.get_or_404(client_id)

        # Optional period filter for recent investments card (ri_start / ri_end)
        ri_start = None
        ri_end = None
        try:
            rs = request.args.get('ri_start', '').strip()
            re_ = request.args.get('ri_end', '').strip()
            if rs:
                ri_start = datetime.strptime(rs, '%Y-%m-%d').date()
            if re_:
                ri_end = datetime.strptime(re_, '%Y-%m-%d').date()
        except ValueError:
            pass  # bad dates → fall back to defaults

        recent_investments, total_period_invested = _recent_investments_for_summary(
            client_id, start_date=ri_start, end_date=ri_end
        )

        # Get all holdings from table
        holdings = Holding.query.filter_by(client_id=client_id).all()

        if not holdings:
            return jsonify(
                {
                    'success': True,
                    'data': {
                        'total_holdings': 0,
                        'total_value': 0,
                        'total_cost': 0,
                        'total_unrealized_pnl': 0,
                        'total_unrealized_pnl_percent': 0,
                        'top_holdings': [],
                        'wealth_creators': [],
                        'most_profitable_by_percent': [],
                        'wealth_destructors': [],
                        'smallest_holdings': [],
                        'sector_breakdown': {},
                        'recent_investments': recent_investments,
                        'total_period_invested': total_period_invested,
                    },
                    'message': f'No holdings found for client {client_id}',
                }
            )

        # Calculate values for each holding (live prices — align with /holdings list endpoint)
        holdings_with_calculations = []
        total_value = 0.0
        total_cost = 0.0
        total_unrealized_pnl = 0.0

        for holding in holdings:
            security = Security.query.get(holding.security_id)
            if security:
                quantity = float(holding.quantity) if holding.quantity else 0
                try:
                    pd = PriceService.get_price(security.id)
                    if getattr(pd, 'is_valid', False) and pd.price is not None:
                        current_price = float(pd.price)
                    else:
                        current_price = (
                            float(security.current_price) if security.current_price else 0.0
                        )
                except Exception:
                    current_price = float(security.current_price) if security.current_price else 0.0
                average_price = float(holding.average_price) if holding.average_price else 0

                current_value = quantity * current_price
                total_cost_holding = quantity * average_price
                unrealized_pnl = current_value - total_cost_holding
                unrealized_pnl_percent = (
                    (unrealized_pnl / total_cost_holding * 100) if total_cost_holding > 0 else 0
                )

                holding_data = {
                    'security_id': holding.security_id,
                    'security_name': security.name,
                    'security_symbol': security.symbol,
                    'quantity': quantity,
                    'current_value': current_value,
                    'unrealized_pnl': unrealized_pnl,
                    'unrealized_pnl_percent': unrealized_pnl_percent,
                    'total_cost': total_cost_holding,
                }

                holdings_with_calculations.append(holding_data)
                total_value += current_value
                total_cost += total_cost_holding
                total_unrealized_pnl += unrealized_pnl

        # Top 5 holdings by current value
        top_holdings = sorted(
            holdings_with_calculations, key=lambda h: h['current_value'], reverse=True
        )[:5]

        # Top 5 wealth creators (highest positive P&L ₹)
        wealth_creators = [h for h in holdings_with_calculations if h['unrealized_pnl'] > 0]
        wealth_creators = sorted(wealth_creators, key=lambda h: h['unrealized_pnl'], reverse=True)[
            :5
        ]

        # Top 5 by gain % (profitable only)
        eligible_pct = [
            h
            for h in holdings_with_calculations
            if h['total_cost'] > 0 and h['unrealized_pnl'] > 0
        ]
        most_profitable_by_percent = sorted(
            eligible_pct, key=lambda h: h['unrealized_pnl_percent'], reverse=True
        )[:5]

        # Top 5 wealth destructors (lowest negative P&L)
        wealth_destructors = [h for h in holdings_with_calculations if h['unrealized_pnl'] < 0]
        wealth_destructors = sorted(wealth_destructors, key=lambda h: h['unrealized_pnl'])[:5]

        # Top 5 smallest holdings by current value
        smallest_holdings = sorted(holdings_with_calculations, key=lambda h: h['current_value'])[:5]

        # Calculate sector breakdown
        sector_breakdown = {}
        for holding in holdings_with_calculations:
            security = Security.query.get(holding['security_id'])
            sector = _security_sector(security)

            if sector not in sector_breakdown:
                sector_breakdown[sector] = {
                    'count': 0,
                    'total_value': 0,
                    'total_cost': 0,
                    'unrealized_pnl': 0,
                }

            sector_breakdown[sector]['count'] += 1
            sector_breakdown[sector]['total_value'] += holding['current_value']
            sector_breakdown[sector]['total_cost'] += holding['total_cost']
            sector_breakdown[sector]['unrealized_pnl'] += holding['unrealized_pnl']

        # Prepare summary data
        summary_data = {
            'total_holdings': len(holdings_with_calculations),
            'total_value': total_value,
            'total_cost': total_cost,
            'total_unrealized_pnl': total_unrealized_pnl,
            'total_unrealized_pnl_percent': (total_unrealized_pnl / total_cost * 100)
            if total_cost > 0
            else 0,
            'top_holdings': top_holdings,
            'wealth_creators': wealth_creators,
            'most_profitable_by_percent': most_profitable_by_percent,
            'wealth_destructors': wealth_destructors,
            'smallest_holdings': smallest_holdings,
            'sector_breakdown': sector_breakdown,
            'recent_investments': recent_investments,
            'total_period_invested': total_period_invested,
        }

        return jsonify(
            {
                'success': True,
                'data': summary_data,
                'message': f'Holdings summary for client {client_id}',
            }
        )
        
    except Exception as e:
        logger.error(f"Error getting V2 holdings summary for client {client_id}: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Failed to get holdings summary: {str(e)}'
        }), 500

