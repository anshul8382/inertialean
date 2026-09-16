#!/usr/bin/env python3
"""
Cashflow Upload API
Handles cashflow file uploads with date parsing and validation
"""

import pandas as pd
import numpy as np
from datetime import datetime, date
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import Cashflow, Client
import logging
import re
from services.secure_upload import sanitize_upload_filename
from typing import List, Dict, Any, Union

# Mounted under api_v1 at url_prefix="/cashflows" → /api/v1/cashflows/...
cashflows_bp = Blueprint('cashflows', __name__)
logger = logging.getLogger(__name__)


def _validated_cashflow_upload_name(file_storage):
    """Return (lowercase_safe_name, error_response) — error_response is a Flask tuple or None."""
    if not file_storage or not file_storage.filename:
        return None, (jsonify({'success': False, 'error': 'No file selected'}), 400)
    try:
        return sanitize_upload_filename(file_storage.filename).lower(), None
    except ValueError as exc:
        return None, (jsonify({'success': False, 'error': str(exc)}), 400)


def _read_cashflow_dataframe(file_storage, filename_lower: str) -> pd.DataFrame:
    """
    Load spreadsheet for cashflow upload.

    Excel: read all cells as str so locale-specific date displays (e.g. 01-12-2025
    meaning 1 Dec) are not converted to datetimes as US MM-DD (12 Jan) by pandas.
    """
    if filename_lower.endswith((".xlsx", ".xls")):
        try:
            return pd.read_excel(file_storage, dtype=str, keep_default_na=False)
        except Exception as exc:
            logger.warning("read_excel(dtype=str) failed (%s); retrying default parser", exc)
            try:
                file_storage.seek(0)
            except Exception:
                try:
                    file_storage.stream.seek(0)
                except Exception:
                    pass
            return pd.read_excel(file_storage)
    if filename_lower.endswith(".csv"):
        return pd.read_csv(file_storage, dtype=str, keep_default_na=False)
    raise ValueError("Unsupported file format")


def parse_date(date_val: Union[str, datetime, pd.Timestamp, Any]) -> date:
    """
    Parse date string in various formats to date object
    
    Supported formats:
    - "9-May-2017"
    - "15-Dec-2023"
    - "1-Jan-2000"
    - "31-Dec-2023"
    - "01-12-2025" (DD-MM-YYYY, day first — common in Excel formula bar in India/EU)
    - "2025-12-01" or "2025-12-01 00:00:00" (ISO)
    """
    if not date_val or pd.isna(date_val):
        raise ValueError("Empty date")

    # Already-normalised datetime (e.g. CSV). Prefer string reads from Excel (dtype=str) for ambiguous dates.
    if isinstance(date_val, (datetime, pd.Timestamp)):
        return pd.Timestamp(date_val).normalize().to_pydatetime().date()

    date_str = str(date_val).strip()
    # Normalise unicode dashes to ASCII hyphen (regex expects -)
    date_str = date_str.replace("\u2011", "-").replace("\u2010", "-").replace("\u2212", "-")

    # Month mapping
    month_map = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    
    try:
        # Try different patterns (day-first for ambiguous numeric dates — India/EU Excel)
        patterns = [
            (r'^(\d{1,2})-([a-zA-Z]{3})-(\d{4})$', "dmy_text"),  # 9-May-2017
            (r'^(\d{1,2})/([a-zA-Z]{3})/(\d{4})$', "dmy_text"),  # 9/May/2017
            (r'^(\d{1,2})\.([a-zA-Z]{3})\.(\d{4})$', "dmy_text"),  # 9.May.2017
            (r'^(\d{1,2})-(\d{1,2})-(\d{4})$', "dmy_num"),  # 01-12-2025 = 1 Dec (NOT US 12 Jan)
            (r'^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s|T|$)', "ymd"),  # 2017-05-09 or ISO + time
            (r'^(\d{1,2})/(\d{1,2})/(\d{4})$', "dmy_slash"),  # 01/12/2025 = 1 Dec
        ]

        for pattern, kind in patterns:
            match = re.match(pattern, date_str)
            if match:
                groups = match.groups()

                if kind == "ymd":
                    year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                elif kind == "dmy_text":
                    day, month_str, year = int(groups[0]), groups[1].lower()[:3], int(groups[2])
                    month = month_map.get(month_str)
                    if not month:
                        raise ValueError(f"Invalid month: {month_str}")
                elif kind in ("dmy_num", "dmy_slash"):
                    day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
                else:
                    continue

                return date(year, month, day)

        # Excel serial as plain number string (e.g. "45962") when cell is not date-formatted
        try:
            serial = float(date_str.replace(",", ""))
            if 20000 <= serial <= 80000:
                return pd.to_datetime(serial, unit="d", origin="1899-12-30", utc=False).date()
        except (ValueError, TypeError, OverflowError, OSError):
            pass
        
        # If no pattern matches, use safe date parsing to avoid timezone conversion
        from utils.date_utils import parse_date_safe
        parsed_date = parse_date_safe(date_str, dayfirst=True)
        return parsed_date
        
    except Exception as e:
        raise ValueError(f"Invalid date format: {date_str} - {str(e)}")

def parse_amount(amount_str: Any) -> float:
    """
    Parse amount string with sign to float
    
    Examples:
    - "+50000" -> 50000.0
    - "-25000" -> -25000.0
    - "50000" -> 50000.0 (assumed positive)
    """
    if pd.isna(amount_str):
        raise ValueError("Empty amount")
    
    amount_str = str(amount_str).strip()
    
    # Remove currency symbols and commas
    amount_str = re.sub(r'[₹$,]', '', amount_str)
    
    # Check for sign
    if amount_str.startswith('+'):
        amount_str = amount_str[1:]
        sign = 1
    elif amount_str.startswith('-'):
        amount_str = amount_str[1:]
        sign = -1
    else:
        sign = 1  # Assume positive if no sign
    
    try:
        amount = float(amount_str) * sign
        return amount
    except ValueError:
        raise ValueError(f"Invalid amount format: {amount_str}")

def validate_cashflow_data(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Validate cashflow data and return validation results
    Expected format: Date, Amount, asset_type
    """
    errors = []
    processed_data = []
    
    # Check for required columns
    df_columns = [col.lower().strip() for col in df.columns]
    
    date_col = None
    amount_col = None
    asset_type_col = None
    description_col = None
    client_col = None
    
    # Find columns (case insensitive)
    for i, col in enumerate(df_columns):
        if 'date' in col:
            date_col = df.columns[i]
        elif 'amount' in col or 'value' in col or 'cashflow' in col:
            amount_col = df.columns[i]
        elif 'asset_type' in col or 'assettype' in col or 'asset' in col:
            asset_type_col = df.columns[i]
        elif 'description' in col or 'desc' in col or 'note' in col or 'comment' in col:
            description_col = df.columns[i]
        elif 'client' in col or 'name' in col:
            client_col = df.columns[i]
    
    if not date_col:
        errors.append({"row": 0, "message": "No date column found. Expected column name containing 'date'"})
        return {"errors": errors, "processed_data": []}
    
    if not amount_col:
        errors.append({"row": 0, "message": "No amount column found. Expected column name containing 'amount', 'value', or 'cashflow'"})
        return {"errors": errors, "processed_data": []}
    
    # Get valid asset classes from database
    from models import AssetClass
    valid_asset_classes = {ac.name.lower(): ac.name for ac in AssetClass.query.all()}
    
    # Process each row
    for idx, row in df.iterrows():
        row_errors = []
        
        try:
            # Parse date (format: 1-Nov-2023)
            parsed_date = parse_date(row[date_col])
            
            # Parse amount (negative = investment, positive = withdrawal)
            parsed_amount = float(row[amount_col])
            # Remove any currency symbols and commas
            if isinstance(parsed_amount, str):
                parsed_amount = parse_amount(parsed_amount)
            
            # Get asset_type (required)
            asset_type = ""
            if asset_type_col and not pd.isna(row[asset_type_col]):
                asset_type = str(row[asset_type_col]).strip()
                
                # Validate asset_type against AssetClass names
                if asset_type.lower() not in valid_asset_classes:
                    # Try to find a close match
                    found_match = False
                    for valid_name, display_name in valid_asset_classes.items():
                        if asset_type.lower() in valid_name or valid_name in asset_type.lower():
                            asset_type = display_name
                            found_match = True
                            break
                    
                    if not found_match:
                        row_errors.append(f"Invalid asset_type '{asset_type}'. Valid types: {', '.join(sorted(set(valid_asset_classes.values())))}")
                else:
                    asset_type = valid_asset_classes[asset_type.lower()]
            
            # Build description with asset_type
            description_parts = []
            if asset_type:
                description_parts.append(f"Asset Type: {asset_type}")
            if description_col and not pd.isna(row[description_col]):
                desc_text = str(row[description_col]).strip()
                if desc_text:
                    description_parts.append(desc_text)
            
            description = " | ".join(description_parts) if description_parts else ""
            
            # Get client info (optional, usually from dropdown)
            client_name = ""
            if client_col and not pd.isna(row[client_col]):
                client_name = str(row[client_col]).strip()
            
            processed_data.append({
                "row": idx + 1,
                "date": parsed_date,
                "amount": parsed_amount,  # Negative = investment, positive = withdrawal
                "description": description,
                "asset_type": asset_type,
                "client_name": client_name,
                "original_row": row.to_dict()
            })
            
        except Exception as e:
            row_errors.append(f"Row {idx + 1}: {str(e)}")
        
        if row_errors:
            errors.extend([{"row": idx + 1, "message": error} for error in row_errors])
    
    return {
        "errors": errors,
        "processed_data": processed_data,
        "columns": {
            "date": date_col,
            "amount": amount_col,
            "asset_type": asset_type_col,
            "description": description_col,
            "client": client_col
        }
    }

@cashflows_bp.route('/preview', methods=['POST'])
@login_required
def preview_cashflow_file():
    """Preview cashflow file before saving"""
    try:
        if 'cashflow_file' not in request.files:
            return jsonify({
                'success': False,
                'error': 'No file provided'
            }), 400
        
        file = request.files['cashflow_file']
        filename, err = _validated_cashflow_upload_name(file)
        if err:
            return err[0], err[1]

        # Read file based on extension
        try:
            if not filename.endswith((".xlsx", ".xls", ".csv")):
                return jsonify(
                    {
                        "success": False,
                        "error": "Unsupported file format. Please use Excel (.xlsx, .xls) or CSV (.csv)",
                    }
                ), 400
            df = _read_cashflow_dataframe(file, filename)
        except Exception as e:
            return jsonify(
                {"success": False, "error": f"Error reading file: {str(e)}"}
            ), 400

        if df.empty:
            return jsonify({
                'success': False,
                'error': 'File is empty'
            }), 400
        
        # Validate data
        validation_result = validate_cashflow_data(df)
        errors = validation_result['errors']
        processed_data = validation_result['processed_data']
        
        # Get client ID from request
        client_id = request.form.get('client_id')
        client = None
        if client_id:
            client = Client.query.get(client_id)
        
        # Prepare preview data
        preview_rows = []
        for item in processed_data:
            amount = item['amount']
            cashflow_type = 'INFLOW (Investment)' if amount < 0 else 'OUTFLOW (Withdrawal)'
            preview_rows.append({
                'row': item['row'],
                'date': item['date'].strftime('%d-%b-%Y'),
                'amount': float(amount),
                'amount_display': f"₹{abs(amount):,.2f}",
                'type': cashflow_type,
                'asset_type': item.get('asset_type', 'N/A'),
                'description': item.get('description', '')
            })
        
        return jsonify({
            'success': True,
            'total_rows': len(df),
            'valid_rows': len(processed_data),
            'error_rows': len(errors),
            'client_id': client_id,
            'client_name': client.name if client else None,
            'preview_data': preview_rows,
            'errors': errors,
            'columns': validation_result.get('columns', {})
        })
        
    except Exception as e:
        logger.error(f"Error previewing cashflow file: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error processing file: {str(e)}'
        }), 500

@cashflows_bp.route('/upload', methods=['POST'])
@login_required
def upload_cashflow_file():
    """Upload and process cashflow file"""
    try:
        if 'cashflow_file' not in request.files:
            return jsonify({
                'success': False,
                'error': 'No file provided'
            }), 400
        
        file = request.files['cashflow_file']
        filename, err = _validated_cashflow_upload_name(file)
        if err:
            return err[0], err[1]

        # Read file based on extension
        try:
            if not filename.endswith((".xlsx", ".xls", ".csv")):
                return jsonify(
                    {
                        "success": False,
                        "error": "Unsupported file format. Please use Excel (.xlsx, .xls) or CSV (.csv)",
                    }
                ), 400
            df = _read_cashflow_dataframe(file, filename)
        except Exception as e:
            return jsonify(
                {"success": False, "error": f"Error reading file: {str(e)}"}
            ), 400

        if df.empty:
            return jsonify({
                'success': False,
                'error': 'File is empty'
            }), 400
        
        # Validate data
        validation_result = validate_cashflow_data(df)
        errors = validation_result['errors']
        processed_data = validation_result['processed_data']
        
        if not processed_data:
            return jsonify({
                'success': False,
                'error': 'No valid data found',
                'errors': errors
            }), 400
        
        # Get client from request or file data
        client_id = request.form.get('client_id')
        client = None
        
        if client_id:
            # Use client selected from dropdown
            client = Client.query.get(client_id)
            if not client:
                return jsonify({
                    'success': False,
                    'error': 'Selected client not found'
                }), 400
            
            # Allow access to all clients (no permission check)
        else:
            # Fallback: use client from file or create default
            if processed_data and processed_data[0]['client_name']:
                client = Client.query.filter_by(name=processed_data[0]['client_name']).first()
                if not client:
                    # Create new client from file data
                    client = Client(
                        name=processed_data[0]['client_name'],
                        email=f"{processed_data[0]['client_name'].lower().replace(' ', '.')}@example.com",
                        phone="",
                        created_by=current_user.id
                    )
                    db.session.add(client)
                    db.session.flush()
            else:
                # Create default client
                client = Client(
                    name=f"{current_user.username}_default",
                    email=f"{current_user.username}@example.com",
                    phone="",
                    created_by=current_user.id
                )
                db.session.add(client)
                db.session.flush()

        # Use CashflowService to create cashflows (centralized, protected)
        from services.cashflow_service import CashflowService
        
        # Prepare cashflow data for service
        # Note: Negative amounts = investment (INFLOW), Positive amounts = withdrawal (OUTFLOW)
        cashflows_data = []
        for item in processed_data:
            amount = item['amount']
            # Determine type based on amount sign
            # Negative = investment (money in) = INFLOW
            # Positive = withdrawal (money out) = OUTFLOW
            cashflow_type = 'INFLOW' if amount < 0 else 'OUTFLOW'
            
            cashflows_data.append({
                'row': item['row'],
                'date': item['date'],
                'amount': amount,  # Keep as-is (negative for investment, positive for withdrawal)
                'description': item['description'],
                'type': cashflow_type
            })
        
        # Create cashflows using service
        result = CashflowService.create_manual_cashflows(
            client_id=client.id,
            cashflows_data=cashflows_data,
            created_by=current_user.id
        )
        
        # Commit changes (service added to session, but we commit here)
        try:
            db.session.commit()
            logger.info(f"Successfully saved {result['saved_count']} cashflow entries via CashflowService")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Database commit error: {str(e)}")
            return jsonify({
                'success': False,
                'error': f'Database error: {str(e)}'
            }), 500
        
        # Extract results from service
        saved_count = result['saved_count']
        skipped_count = result['skipped_count']
        save_errors = result['errors']
        
        # Prepare response
        response_data = {
            'success': True,
            'total_rows': len(df),
            'processed_rows': len(processed_data),
            'saved_rows': saved_count,
            'skipped_rows': skipped_count,
            'error_rows': len(errors) + len(save_errors),
            'client_id': client.id if client else None,
            'errors': errors + save_errors,
            'preview_data': [
                {
                    'date': item['date'].strftime('%d-%b-%Y'),
                    'amount': item['amount'],
                    'description': item['description']
                }
                for item in processed_data[:10]  # First 10 items for preview
            ]
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"Cashflow upload error: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': f'Upload failed: {str(e)}'
        }), 500

@cashflows_bp.route('/list/<int:client_id>')
@login_required
def list_cashflows(client_id):
    """List cashflows for a client"""
    try:
        client = Client.query.get_or_404(client_id)
        
        # Allow access to all clients (no permission check)
        
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date.desc()).all()
        
        cashflow_data = []
        for cf in cashflows:
            cashflow_data.append({
                'id': cf.id,
                'date': cf.date.strftime('%d-%b-%Y'),
                'amount': cf.amount,
                'description': cf.description,
                'created_at': cf.created_at.strftime('%d-%b-%Y %H:%M') if cf.created_at else None
            })
        
        return jsonify({
            'success': True,
            'client_name': client.name,
            'total_cashflows': len(cashflow_data),
            'cashflows': cashflow_data
        })
        
    except Exception as e:
        logger.error(f"List cashflows error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Failed to list cashflows: {str(e)}'
        }), 500