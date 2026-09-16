"""
Billing Calculation Service
Handles all billing calculations based on agreement terms and portfolio values
"""

from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from flask import current_app
from models import (
    Agreement, AgreementVariables, Client, Holding, AssetClass,
    BillingRateStructure, BillingSchedule, BillingConfiguration
)
from sqlalchemy.orm import joinedload
import logging

logger = logging.getLogger(__name__)

class BillingCalculationService:
    """Service to calculate billing amounts based on agreement terms"""
    
    @classmethod
    def calculate_billing_amount(cls, agreement_id: int, billing_date: date = None) -> Dict:
        """
        Calculate billing amount for an agreement
        
        Args:
            agreement_id: ID of the agreement
            billing_date: Date for billing calculation (defaults to today)
            
        Returns:
            Dict containing billing calculation details
        """
        if not current_app:
            return {'error': 'Application context not available'}
            
        if billing_date is None:
            billing_date = date.today()
            
        try:
            # Get agreement with related data
            agreement = Agreement.query.options(
                joinedload(Agreement.lead),
                joinedload(Agreement.variables)
            ).get(agreement_id)
            
            if not agreement:
                return {'error': f'Agreement {agreement_id} not found'}
                
            # Get client from lead
            client = agreement.lead.client if agreement.lead and agreement.lead.client else None
            if not client:
                return {'error': f'No client found for agreement {agreement_id}'}
                
            # Get billing period
            billing_period = cls._get_billing_period(agreement, billing_date)
            if 'error' in billing_period:
                return billing_period
                
            # Get portfolio values by asset class
            portfolio_data = cls._get_portfolio_values_by_asset_class(client, billing_date)
            if 'error' in portfolio_data:
                return portfolio_data
                
            # Get billing rates for this agreement
            billing_rates = cls._get_billing_rates(agreement_id)
            if 'error' in billing_rates:
                return billing_rates
                
            # Calculate fees for each asset class
            billable_assets = cls._calculate_asset_class_fees(
                portfolio_data['asset_class_values'], 
                billing_rates
            )
            
            # Calculate totals
            subtotal = sum(asset['final_fee'] for asset in billable_assets)
            tax_rate = cls._get_tax_rate()
            tax_amount = subtotal * Decimal(str(tax_rate))
            total_amount = subtotal + tax_amount
            
            return {
                'agreement_id': agreement_id,
                'client_id': client.id,
                'client_name': client.name,
                'billing_period': billing_period,
                'portfolio_summary': {
                    'total_value': float(portfolio_data['total_value']),
                    'billable_value': sum(asset['portfolio_value'] for asset in billable_assets),
                    'excluded_assets': portfolio_data.get('excluded_assets', [])
                },
                'billable_assets': billable_assets,
                'totals': {
                    'subtotal': float(subtotal),
                    'tax_rate': float(tax_rate),
                    'tax_amount': float(tax_amount),
                    'total_amount': float(total_amount)
                },
                'billing_notes': cls._generate_billing_notes(agreement, billable_assets)
            }
            
        except Exception as e:
            logger.error(f"Error calculating billing for agreement {agreement_id}: {str(e)}")
            return {'error': f'Calculation error: {str(e)}'}
    
    @classmethod
    def _get_billing_period(cls, agreement: Agreement, billing_date: date) -> Dict:
        """Get billing period for the given date"""
        try:
            # Get billing frequency from agreement variables
            billing_freq_var = next(
                (var for var in agreement.variables if var.variable_name == 'billing_frequency'), 
                None
            )
            
            if not billing_freq_var:
                # Default to yearly if not specified
                billing_frequency = 'yearly'
            else:
                billing_frequency = billing_freq_var.variable_value
                
            # Calculate billing period based on frequency
            if billing_frequency == 'quarterly':
                # Get quarter start
                quarter = (billing_date.month - 1) // 3 + 1
                period_start = date(billing_date.year, (quarter - 1) * 3 + 1, 1)
                period_end = date(billing_date.year, quarter * 3, 1) + timedelta(days=31)
                period_end = period_end.replace(day=1) - timedelta(days=1)
                
            elif billing_frequency == 'half_yearly':
                if billing_date.month <= 6:
                    period_start = date(billing_date.year, 1, 1)
                    period_end = date(billing_date.year, 6, 30)
                else:
                    period_start = date(billing_date.year, 7, 1)
                    period_end = date(billing_date.year, 12, 31)
                    
            else:  # yearly
                period_start = date(billing_date.year, 1, 1)
                period_end = date(billing_date.year, 12, 31)
                
            return {
                'start': period_start.isoformat(),
                'end': period_end.isoformat(),
                'frequency': billing_frequency
            }
            
        except Exception as e:
            logger.error(f"Error calculating billing period: {str(e)}")
            return {'error': f'Billing period calculation error: {str(e)}'}
    
    @classmethod
    def _get_portfolio_values_by_asset_class(cls, client: Client, billing_date: date) -> Dict:
        """Get portfolio values grouped by asset class"""
        try:
            asset_class_values = {}
            total_value = Decimal('0.0')
            excluded_assets = []
            
            # Get all holdings for the client
            holdings = Holding.query.filter_by(client_id=client.id).all()
            
            for holding in holdings:
                if not holding.security or not holding.security.current_price:
                    continue
                    
                # Calculate current value
                quantity = Decimal(str(holding.quantity)) if holding.quantity else Decimal('0.0')
                current_price = Decimal(str(holding.security.current_price))
                value = quantity * current_price
                
                # Get asset class
                asset_class = holding.security.asset_class
                if not asset_class:
                    excluded_assets.append(holding.security.symbol)
                    continue
                    
                asset_class_name = asset_class.name
                
                # Add to asset class total
                if asset_class_name not in asset_class_values:
                    asset_class_values[asset_class_name] = {
                        'asset_class_id': asset_class.id,
                        'asset_class_name': asset_class_name,
                        'portfolio_value': Decimal('0.0')
                    }
                    
                asset_class_values[asset_class_name]['portfolio_value'] += value
                total_value += value
                
            return {
                'asset_class_values': asset_class_values,
                'total_value': total_value,
                'excluded_assets': excluded_assets
            }
            
        except Exception as e:
            logger.error(f"Error getting portfolio values: {str(e)}")
            return {'error': f'Portfolio calculation error: {str(e)}'}
    
    @classmethod
    def _get_billing_rates(cls, agreement_id: int) -> Dict:
        """Get billing rates for the agreement"""
        try:
            rates = BillingRateStructure.query.filter_by(
                agreement_id=agreement_id, 
                is_active=True
            ).all()
            
            if not rates:
                return {'error': f'No billing rates found for agreement {agreement_id}'}
                
            # Convert to dictionary for easy lookup
            rate_dict = {}
            for rate in rates:
                asset_class_name = rate.asset_class.name
                if asset_class_name not in rate_dict:
                    rate_dict[asset_class_name] = []
                    
                rate_dict[asset_class_name].append({
                    'min_amount': float(rate.min_amount),
                    'max_amount': float(rate.max_amount) if rate.max_amount else None,
                    'rate_percentage': float(rate.rate_percentage),
                    'min_fee': float(rate.min_fee),
                    'max_fee': float(rate.max_fee) if rate.max_fee else None
                })
                
            return rate_dict
            
        except Exception as e:
            logger.error(f"Error getting billing rates: {str(e)}")
            return {'error': f'Billing rates error: {str(e)}'}
    
    @classmethod
    def _calculate_asset_class_fees(cls, asset_class_values: Dict, billing_rates: Dict) -> List[Dict]:
        """Calculate fees for each asset class"""
        billable_assets = []
        
        for asset_class_name, asset_data in asset_class_values.items():
            if asset_class_name not in billing_rates:
                continue  # Skip asset classes without billing rates
                
            portfolio_value = float(asset_data['portfolio_value'])
            asset_class_id = asset_data['asset_class_id']
            
            # Find applicable rate
            applicable_rate = cls._find_applicable_rate(portfolio_value, billing_rates[asset_class_name])
            if not applicable_rate:
                continue
                
            # Calculate fee
            calculated_fee = portfolio_value * applicable_rate['rate_percentage']
            
            # Apply min/max fee constraints
            min_fee = applicable_rate['min_fee']
            max_fee = applicable_rate['max_fee']
            
            final_fee = calculated_fee
            fee_breakdown = "Standard rate applied"
            
            if min_fee > 0 and calculated_fee < min_fee:
                final_fee = min_fee
                fee_breakdown = f"Minimum fee applied (calculated: ₹{calculated_fee:.2f})"
            elif max_fee and calculated_fee > max_fee:
                final_fee = max_fee
                fee_breakdown = f"Maximum fee applied (calculated: ₹{calculated_fee:.2f})"
                
            billable_assets.append({
                'asset_class': asset_class_name,
                'asset_class_id': asset_class_id,
                'portfolio_value': portfolio_value,
                'rate_percentage': applicable_rate['rate_percentage'],
                'calculated_fee': calculated_fee,
                'min_fee': min_fee,
                'max_fee': max_fee,
                'final_fee': final_fee,
                'fee_breakdown': fee_breakdown
            })
            
        return billable_assets
    
    @classmethod
    def _find_applicable_rate(cls, portfolio_value: float, rates: List[Dict]) -> Optional[Dict]:
        """Find the applicable rate for a given portfolio value"""
        for rate in rates:
            min_amount = rate['min_amount']
            max_amount = rate['max_amount']
            
            if portfolio_value >= min_amount:
                if max_amount is None or portfolio_value <= max_amount:
                    return rate
                    
        return None
    
    @classmethod
    def _get_tax_rate(cls) -> float:
        """Get tax rate from configuration"""
        try:
            config = BillingConfiguration.query.filter_by(
                config_key='default_tax_rate',
                is_active=True
            ).first()
            
            if config:
                return float(config.config_value)
            else:
                return 0.18  # Default 18% GST
                
        except Exception as e:
            logger.error(f"Error getting tax rate: {str(e)}")
            return 0.18
    
    @classmethod
    def _generate_billing_notes(cls, agreement: Agreement, billable_assets: List[Dict]) -> List[str]:
        """Generate billing notes based on agreement and calculations"""
        notes = []
        
        # Check for special notes in agreement
        special_note_var = next(
            (var for var in agreement.variables if var.variable_name == 'special_note'), 
            None
        )
        
        if special_note_var and special_note_var.variable_value:
            notes.append(special_note_var.variable_value)
            
        # Add calculation notes
        min_fee_applied = any(asset['final_fee'] == asset['min_fee'] for asset in billable_assets)
        max_fee_applied = any(asset['final_fee'] == asset['max_fee'] for asset in billable_assets)
        
        if min_fee_applied:
            notes.append("Minimum fee thresholds considered")
        if max_fee_applied:
            notes.append("Maximum fee caps applied")
            
        return notes
    
    @classmethod
    def get_billing_schedule(cls, agreement_id: int) -> Dict:
        """Get billing schedule for an agreement"""
        if not current_app:
            return {'error': 'Application context not available'}
            
        try:
            schedule = BillingSchedule.query.filter_by(
                agreement_id=agreement_id,
                is_active=True
            ).first()
            
            if not schedule:
                return {'error': f'No billing schedule found for agreement {agreement_id}'}
                
            return {
                'agreement_id': agreement_id,
                'billing_start_date': schedule.billing_start_date.isoformat(),
                'last_billing_date': schedule.last_billing_date.isoformat() if schedule.last_billing_date else None,
                'next_billing_date': schedule.next_billing_date.isoformat(),
                'billing_cycle_number': schedule.billing_cycle_number,
                'is_active': schedule.is_active
            }
            
        except Exception as e:
            logger.error(f"Error getting billing schedule: {str(e)}")
            return {'error': f'Billing schedule error: {str(e)}'}
    
    @classmethod
    def update_billing_schedule(cls, agreement_id: int, invoice_date: date) -> bool:
        """Update billing schedule after invoice generation"""
        if not current_app:
            return False
            
        try:
            schedule = BillingSchedule.query.filter_by(
                agreement_id=agreement_id,
                is_active=True
            ).first()
            
            if not schedule:
                return False
                
            # Update last billing date
            schedule.last_billing_date = invoice_date
            
            # Calculate next billing date based on frequency
            agreement = Agreement.query.get(agreement_id)
            if not agreement:
                return False
                
            # Get billing frequency
            billing_freq_var = next(
                (var for var in agreement.variables if var.variable_name == 'billing_frequency'), 
                None
            )
            
            frequency = billing_freq_var.variable_value if billing_freq_var else 'yearly'
            
            # Calculate next billing date
            if frequency == 'quarterly':
                next_date = invoice_date + timedelta(days=90)
            elif frequency == 'half_yearly':
                next_date = invoice_date + timedelta(days=180)
            else:  # yearly
                next_date = invoice_date + timedelta(days=365)
                
            schedule.next_billing_date = next_date
            schedule.billing_cycle_number += 1
            
            from app import db
            db.session.commit()
            return True
            
        except Exception as e:
            logger.error(f"Error updating billing schedule: {str(e)}")
            from app import db
            db.session.rollback()
            return False










