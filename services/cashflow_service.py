"""
Cashflow Service
Centralized service for cashflow operations
ALL cashflow modifications must go through this service to ensure data integrity
"""

from models import db, Cashflow, Client
from typing import Dict, List, Any, Optional
from datetime import date, datetime
from sqlalchemy import func
import logging

logger = logging.getLogger(__name__)

# Dummy dates that indicate manual cashflow uploads
# These cashflows are PROTECTED from auto-recalculation
DUMMY_DATES = [
    date(2000, 1, 1),
    date(2000, 1, 2),  # Added: Also handle timezone conversion issues
    date(1900, 1, 1),
    date(1970, 1, 1),  # Unix epoch
]

# Matching-only bands (G8, advisor notes, missing-trade report). XIRR still uses stored sign.
MATCH_SAME_DAY_PCT = 0.01
MATCH_SAME_DAY_FLOOR = 1_000.0
MATCH_NEARBY_PCT = 0.02
MATCH_NEARBY_FLOOR = 2_000.0
MATCH_SETTLEMENT_DAYS = 3
MATCH_CLUB_DAYS = 14


def canonical_cashflow_signed_amount(cashflow) -> float:
    """Signed amount for CF↔trade matching (not XIRR).

    Stored sign is the primary signal (negative = investment, positive = withdrawal).

    INFLOW / INVESTMENT with a **positive** stored amount → treat as investment (−abs).
    That covers form/upload rows tagged Investment but saved as +amount.

    OUTFLOW / WITHDRAWAL with a **negative** stored amount → keep the stored
    negative (do not flip to +). Auto trade cashflows are often mistagged OUTFLOW
    while the amount correctly encodes a BUY.

    Missing type → stored sign unchanged.
    """
    if cashflow is None:
        return 0.0
    if isinstance(cashflow, dict):
        raw = cashflow.get("amount")
        ctype = cashflow.get("type") or cashflow.get("cashflow_type")
    else:
        raw = getattr(cashflow, "amount", None)
        ctype = getattr(cashflow, "type", None)
    try:
        amt = float(raw if raw is not None else 0)
    except (TypeError, ValueError):
        return 0.0
    if amt == 0:
        return 0.0
    t = (str(ctype).strip().upper() if ctype is not None else "")
    # Only correct the common false positive: Investment tagged but amount +.
    if t in ("INFLOW", "INVESTMENT") and amt > 0:
        return -amt
    # Do not force OUTFLOW when amount is already negative (mistagged BUY lines).
    return amt


class CashflowService:
    """
    Centralized service for cashflow operations
    This is the ONLY way to modify cashflow data - no direct DB access allowed
    
    PROTECTION RULE:
    - Cashflows with dummy dates (2000-01-01, etc.) are MANUAL uploads
    - These are NEVER auto-recalculated from trades
    - Only cashflows with real transaction dates can be recalculated
    """
    
    @staticmethod
    def recalculate_for_date(client_id: int, transaction_date: date) -> Dict[str, Any]:
        """
        Incremental update: Recalculate cashflow for a specific client and date
        This is used when transactions are added/updated/deleted
        
        PROTECTION: Skips dummy dates (manual uploads are never recalculated)
        
        Args:
            client_id: Client ID
            transaction_date: Date to recalculate (date object)
            
        Returns:
            Dictionary with operation details
        """
        try:
            from models import Transaction
            
            # PROTECTION: Never recalculate dummy date cashflows (manual uploads)
            if transaction_date in DUMMY_DATES:
                logger.warning(f"Skipping recalculation for dummy date {transaction_date} - manual entry protected")
                return {
                    'action': 'skipped',
                    'date': transaction_date,
                    'client_id': client_id,
                    'reason': 'Dummy date indicates manual upload - protected from auto-recalculation'
                }
            
            # Get all transactions for this client and date
            transactions = Transaction.query.filter(
                Transaction.client_id == client_id,
                func.date(Transaction.transaction_date) == transaction_date
            ).all()
            
            # Calculate net cashflow
            total_cashflow = 0.0
            for t in transactions:
                amt = float(getattr(t, 'amount', float(t.quantity) * float(t.price)))
                if t.type == 'BUY':
                    total_cashflow -= amt  # Outflow
                elif t.type == 'SELL':
                    total_cashflow += amt  # Inflow
                # SPLIT and BONUS don't affect cashflow
            
            # Get existing cashflow for this date
            cashflow = Cashflow.query.filter_by(
                client_id=client_id,
                date=transaction_date
            ).first()
            
            if total_cashflow == 0:
                # If net cashflow is zero, delete existing cashflow if present
                if cashflow:
                    db.session.delete(cashflow)
                    logger.info(f"Deleted zero cashflow for client {client_id} on {transaction_date}")
                    return {
                        'action': 'deleted',
                        'date': transaction_date,
                        'client_id': client_id,
                        'reason': 'Net cashflow is zero'
                    }
                return {
                    'action': 'none',
                    'date': transaction_date,
                    'client_id': client_id,
                    'reason': 'No cashflow needed'
                }
            else:
                # Update or create cashflow
                if not cashflow:
                    # Get a user ID for created_by (using the first admin user or user ID 1)
                    from models import User
                    user = User.query.filter_by(is_admin=True).first() or User.query.first()
                    if not user:
                        logger.error("No user found for created_by field")
                        raise ValueError("No user found for created_by field")
                    
                    # Create new cashflow
                    # Negative amount = investment (money in) = INFLOW, Positive amount = withdrawal (money out) = OUTFLOW
                    cashflow = Cashflow(
                        client_id=client_id,
                        date=transaction_date,
                        amount=float(total_cashflow),
                        type='INFLOW' if total_cashflow < 0 else 'OUTFLOW',
                        description=f"Net cashflow for {transaction_date}",
                        created_by=user.id
                    )
                    db.session.add(cashflow)
                    logger.info(f"Created cashflow for client {client_id} on {transaction_date}: {total_cashflow}")
                    return {
                        'action': 'created',
                        'date': transaction_date,
                        'amount': total_cashflow,
                        'client_id': client_id
                    }
                else:
                    # Update existing cashflow
                    old_amount = cashflow.amount
                    cashflow.amount = float(total_cashflow)
                    # Negative amount = investment (money in) = INFLOW, Positive amount = withdrawal (money out) = OUTFLOW
                    cashflow.type = 'INFLOW' if total_cashflow < 0 else 'OUTFLOW'
                    logger.info(f"Updated cashflow for client {client_id} on {transaction_date}: {old_amount} -> {total_cashflow}")
                    return {
                        'action': 'updated',
                        'date': transaction_date,
                        'old_amount': old_amount,
                        'new_amount': total_cashflow,
                        'client_id': client_id
                    }
        
        except Exception as e:
            logger.error(f"Error recalculating cashflow for client {client_id} on {transaction_date}: {e}")
            raise
    
    @staticmethod
    def recalculate_for_dates(client_id: int, dates: List[date]) -> Dict[str, Any]:
        """
        Batch recalculation for multiple dates
        
        PROTECTION: Automatically filters out dummy dates (manual uploads)
        
        Args:
            client_id: Client ID
            dates: List of dates to recalculate
            
        Returns:
            Dictionary with results for all dates
        """
        # Filter out dummy dates to protect manual uploads
        real_dates = [d for d in dates if d not in DUMMY_DATES]
        dummy_dates = [d for d in dates if d in DUMMY_DATES]
        
        if dummy_dates:
            logger.info(f"Filtered out {len(dummy_dates)} dummy dates to protect manual uploads")
        
        results = []
        for date_obj in real_dates:
            try:
                result = CashflowService.recalculate_for_date(client_id, date_obj)
                results.append(result)
            except Exception as e:
                logger.error(f"Error recalculating for date {date_obj}: {e}")
                results.append({
                    'action': 'error',
                    'date': date_obj,
                    'error': str(e)
                })
        
        return {
            'client_id': client_id,
            'total_dates': len(dates),
            'real_dates_processed': len(real_dates),
            'dummy_dates_filtered': len(dummy_dates),
            'results': results
        }
    
    @staticmethod
    def create_manual_cashflow(
        client_id: int,
        date_obj: date,
        amount: float,
        description: str = "",
        created_by: int = None,
        cashflow_type: str = None
    ) -> Dict[str, Any]:
        """
        Create a manual cashflow entry (from UI or upload)
        This is for cashflows that are NOT derived from transactions
        
        Args:
            client_id: Client ID
            date_obj: Date of cashflow
            amount: Cashflow amount (negative = investment/INFLOW, positive = withdrawal/OUTFLOW)
            description: Description
            created_by: User ID who created this
            cashflow_type: Optional type override (INFLOW/OUTFLOW). If not provided, determined from amount sign.
            
        Returns:
            Dictionary with operation result
        """
        try:
            # Validate amount against schedule rules if schedule exists
            from models import MonthlyInvestmentSchedule
            from services.investment_cycle import validate_amount_for_schedule
            
            schedule = MonthlyInvestmentSchedule.query.filter_by(client_id=client_id).first()
            if not validate_amount_for_schedule(amount, schedule):
                return {
                    'success': False,
                    'error': f'Invalid amount {amount}: Amount must be positive, or schedule must allow zero/negative amounts.'
                }

            # Normalize to midnight datetime so same-calendar-day rows compare cleanly.
            # Multiple cashflows on the same day are allowed (advisor notes, split entries);
            # list views aggregate by date when needed.
            if isinstance(date_obj, datetime):
                cashflow_date = datetime.combine(date_obj.date(), datetime.min.time())
            elif isinstance(date_obj, date):
                cashflow_date = datetime.combine(date_obj, datetime.min.time())
            else:
                return {
                    'success': False,
                    'error': f'Invalid cashflow date: {date_obj!r}',
                }
            
            # Determine type if not provided
            # Negative amount = investment (money in) = INFLOW
            # Positive amount = withdrawal (money out) = OUTFLOW
            if not cashflow_type:
                cashflow_type = 'INFLOW' if amount < 0 else 'OUTFLOW'
            
            # Create new cashflow
            cashflow = Cashflow(
                client_id=client_id,
                date=cashflow_date,
                amount=amount,
                type=cashflow_type,
                description=description,
                created_by=created_by
            )
            db.session.add(cashflow)
            logger.info(f"Created manual cashflow for client {client_id} on {cashflow_date}: {amount}")
            
            return {
                'success': True,
                'action': 'created',
                'cashflow_id': cashflow.id,
                'amount': amount,
                'date': cashflow_date
            }
        
        except Exception as e:
            logger.error(f"Error creating manual cashflow: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def create_manual_cashflows(
        client_id: int,
        cashflows_data: List[Dict[str, Any]],
        created_by: int = None
    ) -> Dict[str, Any]:
        """
        Create multiple manual cashflows (from upload)
        
        Args:
            client_id: Client ID
            cashflows_data: List of cashflow dictionaries with keys: date, amount, description
            created_by: User ID who created these
            
        Returns:
            Dictionary with operation results
        """
        saved_count = 0
        skipped_count = 0
        errors = []
        
        for item in cashflows_data:
            try:
                # Use type from item if provided, otherwise determine from amount sign
                # Negative amount = investment (INFLOW), Positive amount = withdrawal (OUTFLOW)
                cashflow_type = item.get('type')
                amount = item['amount']
                
                if not cashflow_type:
                    # Determine type from amount sign
                    cashflow_type = 'INFLOW' if amount < 0 else 'OUTFLOW'
                
                result = CashflowService.create_manual_cashflow(
                    client_id=client_id,
                    date_obj=item['date'],
                    amount=amount,
                    description=item.get('description', ''),
                    created_by=created_by,
                    cashflow_type=cashflow_type
                )
                
                if result['success']:
                    if result.get('action') == 'created':
                        saved_count += 1
                    elif result.get('action') == 'skipped':
                        skipped_count += 1
                
            except Exception as e:
                errors.append({
                    'row': item.get('row', 'unknown'),
                    'error': str(e)
                })
        
        return {
            'success': True,
            'saved_count': saved_count,
            'skipped_count': skipped_count,
            'error_count': len(errors),
            'errors': errors
        }
    
    @staticmethod
    def delete_cashflow(cashflow_id: int) -> Dict[str, Any]:
        """
        Delete a cashflow entry
        
        Args:
            cashflow_id: Cashflow ID to delete
            
        Returns:
            Dictionary with operation result
        """
        try:
            cashflow = Cashflow.query.get(cashflow_id)
            if not cashflow:
                return {
                    'success': False,
                    'error': 'Cashflow not found'
                }

            # Integrity tables reference cashflow.id; clear before delete to avoid FK errors.
            from models import DataIntegrityIssue, DataIntegrityException

            DataIntegrityIssue.query.filter_by(cashflow_id=cashflow_id).update(
                {"cashflow_id": None},
                synchronize_session=False,
            )
            DataIntegrityException.query.filter_by(cashflow_id=cashflow_id).update(
                {"cashflow_id": None},
                synchronize_session=False,
            )

            db.session.delete(cashflow)
            logger.info(f"Deleted cashflow {cashflow_id}")
            
            return {
                'success': True,
                'cashflow_id': cashflow_id
            }
        
        except Exception as e:
            logger.error(f"Error deleting cashflow {cashflow_id}: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    @staticmethod
    def bulk_delete_cashflows(cashflow_ids: List[int]) -> Dict[str, Any]:
        """
        Delete many cashflows in one session (caller commits).

        Returns:
            success: False if any row hit a non–not-found error (caller should rollback).
            deleted_count, not_found_count, hard_errors (list of str).
        """
        seen: set[int] = set()
        unique_ids: List[int] = []
        for cid in cashflow_ids:
            if cid in seen:
                continue
            seen.add(cid)
            unique_ids.append(cid)

        deleted_count = 0
        not_found_count = 0
        hard_errors: List[str] = []

        for cid in unique_ids:
            result = CashflowService.delete_cashflow(cid)
            if result.get("success"):
                deleted_count += 1
            elif result.get("error") == "Cashflow not found":
                not_found_count += 1
            else:
                hard_errors.append(f"#{cid}: {result.get('error', 'unknown error')}")

        return {
            "success": len(hard_errors) == 0,
            "deleted_count": deleted_count,
            "not_found_count": not_found_count,
            "hard_errors": hard_errors,
        }

    @staticmethod
    def update_cashflow(cashflow_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing cashflow entry
        
        Args:
            cashflow_id: Cashflow ID to update
            data: Dictionary with fields to update (date, amount, description)
            NOTE: client_id cannot be updated - it's immutable for data integrity
            
        Returns:
            Dictionary with operation result
        """
        try:
            cashflow = Cashflow.query.get(cashflow_id)
            if not cashflow:
                return {
                    'success': False,
                    'error': 'Cashflow not found'
                }
            
            # Validate client_id is not being set to None
            if 'client_id' in data:
                if data['client_id'] is None:
                    logger.error(f"Attempted to set client_id to None for cashflow {cashflow_id}")
                    return {
                        'success': False,
                        'error': 'client_id cannot be None. Cashflow must be associated with a client.'
                    }
                # Allow updating client_id only if it's a valid integer
                if isinstance(data['client_id'], int) and data['client_id'] > 0:
                    cashflow.client_id = data['client_id']
                else:
                    logger.error(f"Invalid client_id value: {data['client_id']}")
                    return {
                        'success': False,
                        'error': f'Invalid client_id value: {data["client_id"]}'
                    }
            
            # Update fields
            if 'date' in data:
                date_obj = data['date']
                if isinstance(date_obj, datetime):
                    cashflow.date = datetime.combine(date_obj.date(), datetime.min.time())
                elif isinstance(date_obj, date):
                    cashflow.date = datetime.combine(date_obj, datetime.min.time())
                else:
                    return {
                        'success': False,
                        'error': f'Invalid cashflow date: {date_obj!r}',
                    }
            if 'amount' in data:
                try:
                    amount = float(data['amount'])
                except (TypeError, ValueError):
                    return {
                        'success': False,
                        'error': 'Amount must be a number.',
                    }
                if amount == 0:
                    return {
                        'success': False,
                        'error': 'Amount must be non-zero.',
                    }
                cashflow.amount = amount
                if data.get('cashflow_type') in ('INFLOW', 'OUTFLOW'):
                    cashflow.type = data['cashflow_type']
                else:
                    # Negative amount = investment (money in) = INFLOW
                    cashflow.type = 'INFLOW' if amount < 0 else 'OUTFLOW'
            elif data.get('cashflow_type') in ('INFLOW', 'OUTFLOW'):
                cashflow.type = data['cashflow_type']
            if 'description' in data:
                cashflow.description = data['description']
            
            logger.info(f"Updated cashflow {cashflow_id}")
            
            return {
                'success': True,
                'cashflow_id': cashflow_id,
                'data': {
                    'date': cashflow.date,
                    'amount': cashflow.amount,
                    'type': cashflow.type,
                    'description': cashflow.description,
                    'client_id': cashflow.client_id
                }
            }
        
        except ValueError as e:
            logger.error(f"Validation error updating cashflow {cashflow_id}: {e}")
            return {
                'success': False,
                'error': str(e)
            }
        except Exception as e:
            logger.error(f"Error updating cashflow {cashflow_id}: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def get_cashflows(client_id: int, start_date: date = None, end_date: date = None) -> List[Cashflow]:
        """
        Get cashflows for a client
        
        Args:
            client_id: Client ID
            start_date: Optional start date filter
            end_date: Optional end date filter
            
        Returns:
            List of Cashflow objects
        """
        query = Cashflow.query.filter_by(client_id=client_id)
        
        if start_date:
            query = query.filter(Cashflow.date >= start_date)
        if end_date:
            query = query.filter(Cashflow.date <= end_date)
        
        return query.order_by(Cashflow.date).all()

    AGGREGATE_PERIODS = frozenset({"date", "month", "year"})

    @staticmethod
    def _period_key_and_label(cf_date: datetime, period: str) -> tuple:
        """Return (sort_key, display_label) for a cashflow date and aggregate period."""
        if isinstance(cf_date, datetime):
            d = cf_date.date()
        else:
            d = cf_date
        if period == "date":
            key = d.isoformat()
            return key, key
        if period == "month":
            key = f"{d.year:04d}-{d.month:02d}"
            return key, d.strftime("%b %Y")
        if period == "year":
            key = f"{d.year:04d}"
            return key, key
        raise ValueError(f"Unsupported aggregate period: {period}")

    @staticmethod
    def _signed_amount(cashflow) -> float:
        """Netting uses stored signed amount (negative=INFLOW, positive=OUTFLOW)."""
        return float(cashflow.amount)

    @staticmethod
    def aggregate_cashflows(
        cashflows: List[Any],
        period: str,
        *,
        group_by_client: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Net cashflows by date, month, or year.

        When group_by_client is False (All Clients), rows in the same period are
        netted across clients into one aggregate row.

        Returns list of dicts sorted by period desc (then client name):
          period_key, period_label, client_id, client_name, client_count,
          net_amount, type, row_count
        Zero-net groups are omitted.
        """
        if period not in CashflowService.AGGREGATE_PERIODS:
            raise ValueError(f"period must be one of {sorted(CashflowService.AGGREGATE_PERIODS)}")

        buckets: Dict[Any, Dict[str, Any]] = {}
        for cf in cashflows:
            if cf.date is None:
                continue
            period_key, period_label = CashflowService._period_key_and_label(cf.date, period)
            client_id = getattr(cf, "client_id", None)
            if group_by_client:
                bucket_key = (period_key, client_id)
            else:
                bucket_key = (period_key,)

            bucket = buckets.get(bucket_key)
            if bucket is None:
                client = getattr(cf, "client", None)
                client_name = getattr(client, "name", None) or (f"Client #{client_id}" if client_id else "Unknown")
                bucket = {
                    "period_key": period_key,
                    "period_label": period_label,
                    "client_id": client_id if group_by_client else None,
                    "client_name": client_name if group_by_client else "All clients",
                    "client_ids": set(),
                    "net_amount": 0.0,
                    "row_count": 0,
                }
                buckets[bucket_key] = bucket

            bucket["net_amount"] += CashflowService._signed_amount(cf)
            bucket["row_count"] += 1
            if client_id is not None:
                bucket["client_ids"].add(client_id)
            if group_by_client and bucket.get("client_name") in (None, f"Client #{client_id}"):
                client = getattr(cf, "client", None)
                if client and getattr(client, "name", None):
                    bucket["client_name"] = client.name

        rows: List[Dict[str, Any]] = []
        for bucket in buckets.values():
            net = round(bucket["net_amount"], 2)
            if net == 0:
                continue
            client_ids = bucket.pop("client_ids")
            client_count = len(client_ids)
            if not group_by_client:
                bucket["client_name"] = (
                    f"All clients ({client_count})" if client_count else "All clients"
                )
            bucket["client_count"] = client_count
            bucket["net_amount"] = net
            # Convention: negative = INFLOW, positive = OUTFLOW
            bucket["type"] = "INFLOW" if net < 0 else "OUTFLOW"
            rows.append(bucket)

        rows.sort(key=lambda r: (r["period_key"], r.get("client_name") or ""), reverse=True)
        return rows


