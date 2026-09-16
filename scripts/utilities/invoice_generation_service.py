"""
Invoice Generation Service
Handles invoice generation with itemized details and PDF creation
"""

from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional
from flask import current_app
from models import (
    Agreement, Client, Invoice, InvoiceLineItem, BillingConfiguration
)
from billing_calculation_service import BillingCalculationService
import logging

logger = logging.getLogger(__name__)

class InvoiceGenerationService:
    """Service to generate invoices with itemized details"""
    
    @classmethod
    def generate_invoice(cls, agreement_id: int, billing_date: date = None, user_id: int = None) -> Dict:
        """
        Generate complete invoice with itemized details
        
        Args:
            agreement_id: ID of the agreement
            billing_date: Date for invoice generation (defaults to today)
            user_id: ID of user creating the invoice
            
        Returns:
            Dict containing complete invoice details
        """
        if not current_app:
            return {'error': 'Application context not available'}
            
        if billing_date is None:
            billing_date = date.today()
            
        if user_id is None:
            return {'error': 'User ID is required for invoice generation'}
            
        try:
            # Calculate billing amount
            billing_calculation = BillingCalculationService.calculate_billing_amount(
                agreement_id, billing_date
            )
            
            if 'error' in billing_calculation:
                return billing_calculation
                
            # Generate invoice number
            invoice_number = cls._generate_invoice_number(agreement_id, billing_date)
            
            # Get due date
            due_date = cls._calculate_due_date(billing_date)
            
            # Create invoice record
            invoice = cls._create_invoice_record(
                agreement_id, billing_calculation, invoice_number, 
                billing_date, due_date, user_id
            )
            
            if 'error' in invoice:
                return invoice
                
            # Create line items
            line_items = cls._create_line_items(
                invoice['invoice_id'], billing_calculation['billable_assets']
            )
            
            if 'error' in line_items:
                return line_items
                
            # Update billing schedule
            BillingCalculationService.update_billing_schedule(agreement_id, billing_date)
            
            # Return complete invoice details
            return {
                'invoice_id': invoice['invoice_id'],
                'invoice_number': invoice_number,
                'invoice_date': billing_date.isoformat(),
                'due_date': due_date.isoformat(),
                'client_info': {
                    'id': billing_calculation['client_id'],
                    'name': billing_calculation['client_name']
                },
                'agreement_info': {
                    'id': agreement_id
                },
                'billing_period': billing_calculation['billing_period'],
                'line_items': line_items['line_items'],
                'totals': billing_calculation['totals'],
                'billing_notes': billing_calculation['billing_notes'],
                'status': 'draft'
            }
            
        except Exception as e:
            logger.error(f"Error generating invoice for agreement {agreement_id}: {str(e)}")
            return {'error': f'Invoice generation error: {str(e)}'}
    
    @classmethod
    def _generate_invoice_number(cls, agreement_id: int, billing_date: date) -> str:
        """Generate unique invoice number"""
        try:
            # Get invoice number format from configuration
            format_config = BillingConfiguration.query.filter_by(
                config_key='invoice_number_format',
                is_active=True
            ).first()
            
            prefix_config = BillingConfiguration.query.filter_by(
                config_key='invoice_number_prefix',
                is_active=True
            ).first()
            
            prefix = prefix_config.config_value if prefix_config else 'INV'
            format_str = format_config.config_value if format_config else '{prefix}-{year}-{quarter}-{sequence}'
            
            # Get next sequence number
            sequence = cls._get_next_invoice_sequence(billing_date)
            
            # Format invoice number
            year = billing_date.year
            quarter = (billing_date.month - 1) // 3 + 1
            
            invoice_number = format_str.format(
                prefix=prefix,
                year=year,
                quarter=quarter,
                sequence=sequence
            )
            
            return invoice_number
            
        except Exception as e:
            logger.error(f"Error generating invoice number: {str(e)}")
            # Fallback to simple format
            return f"INV-{billing_date.year}-{billing_date.month:02d}-{agreement_id}"
    
    @classmethod
    def _get_next_invoice_sequence(cls, billing_date: date) -> int:
        """Get next sequence number for invoice"""
        try:
            # Count existing invoices for the same period
            year = billing_date.year
            quarter = (billing_date.month - 1) // 3 + 1
            
            # Count invoices in the same quarter
            quarter_start = date(year, (quarter - 1) * 3 + 1, 1)
            quarter_end = date(year, quarter * 3, 1) + timedelta(days=31)
            quarter_end = quarter_end.replace(day=1) - timedelta(days=1)
            
            from models import Invoice
            count = Invoice.query.filter(
                Invoice.invoice_date >= quarter_start,
                Invoice.invoice_date <= quarter_end
            ).count()
            
            return count + 1
            
        except Exception as e:
            logger.error(f"Error getting invoice sequence: {str(e)}")
            return 1
    
    @classmethod
    def _calculate_due_date(cls, billing_date: date) -> date:
        """Calculate invoice due date"""
        try:
            # Get due days from configuration
            due_days_config = BillingConfiguration.query.filter_by(
                config_key='invoice_due_days',
                is_active=True
            ).first()
            
            due_days = int(due_days_config.config_value) if due_days_config else 15
            
            return billing_date + timedelta(days=due_days)
            
        except Exception as e:
            logger.error(f"Error calculating due date: {str(e)}")
            return billing_date + timedelta(days=15)
    
    @classmethod
    def _create_invoice_record(cls, agreement_id: int, billing_calculation: Dict, 
                              invoice_number: str, invoice_date: date, 
                              due_date: date, user_id: int) -> Dict:
        """Create invoice record in database"""
        try:
            from app import db
            from models import Invoice
            
            # Get tax rate
            tax_rate = billing_calculation['totals']['tax_rate']
            tax_amount = billing_calculation['totals']['tax_amount']
            total_amount = billing_calculation['totals']['total_amount']
            net_amount = billing_calculation['totals']['subtotal']
            
            # Create invoice
            invoice = Invoice(
                agreement_id=agreement_id,
                client_id=billing_calculation['client_id'],
                invoice_number=invoice_number,
                invoice_date=invoice_date,
                billing_period_start=datetime.strptime(billing_calculation['billing_period']['start'], '%Y-%m-%d').date(),
                billing_period_end=datetime.strptime(billing_calculation['billing_period']['end'], '%Y-%m-%d').date(),
                total_amount=Decimal(str(total_amount)),
                tax_rate=Decimal(str(tax_rate)),
                tax_amount=Decimal(str(tax_amount)),
                net_amount=Decimal(str(net_amount)),
                status='draft',
                due_date=due_date,
                created_by=user_id
            )
            
            db.session.add(invoice)
            db.session.flush()  # Get the ID
            
            return {'invoice_id': invoice.id}
            
        except Exception as e:
            logger.error(f"Error creating invoice record: {str(e)}")
            from app import db
            db.session.rollback()
            return {'error': f'Invoice record creation error: {str(e)}'}
    
    @classmethod
    def _create_line_items(cls, invoice_id: int, billable_assets: List[Dict]) -> Dict:
        """Create invoice line items"""
        try:
            from app import db
            from models import InvoiceLineItem
            
            line_items = []
            
            for asset in billable_assets:
                line_item = InvoiceLineItem(
                    invoice_id=invoice_id,
                    asset_class_id=asset['asset_class_id'],
                    asset_class_name=asset['asset_class'],
                    portfolio_value=Decimal(str(asset['portfolio_value'])),
                    rate_percentage=Decimal(str(asset['rate_percentage'])),
                    calculated_fee=Decimal(str(asset['calculated_fee'])),
                    min_fee_applied=Decimal(str(asset['min_fee'])),
                    max_fee_applied=Decimal(str(asset['max_fee'])),
                    final_fee=Decimal(str(asset['final_fee'])),
                    fee_breakdown=asset['fee_breakdown']
                )
                
                db.session.add(line_item)
                line_items.append({
                    'description': f"Investment Advisory Fee - {asset['asset_class']} Assets",
                    'asset_class': asset['asset_class'],
                    'portfolio_value': asset['portfolio_value'],
                    'rate': f"{asset['rate_percentage'] * 100:.2f}%",
                    'amount': asset['final_fee'],
                    'fee_breakdown': asset['fee_breakdown']
                })
            
            db.session.commit()
            
            return {'line_items': line_items}
            
        except Exception as e:
            logger.error(f"Error creating line items: {str(e)}")
            from app import db
            db.session.rollback()
            return {'error': f'Line items creation error: {str(e)}'}
    
    @classmethod
    def get_invoice(cls, invoice_id: int) -> Dict:
        """Get invoice details by ID"""
        if not current_app:
            return {'error': 'Application context not available'}
            
        try:
            from models import Invoice
            
            invoice = Invoice.query.get(invoice_id)
            if not invoice:
                return {'error': f'Invoice {invoice_id} not found'}
                
            return {
                'invoice_id': invoice.id,
                'invoice_number': invoice.invoice_number,
                'invoice_date': invoice.invoice_date.isoformat(),
                'due_date': invoice.due_date.isoformat(),
                'client_info': {
                    'id': invoice.client_id,
                    'name': invoice.client.name if invoice.client else 'Unknown'
                },
                'agreement_info': {
                    'id': invoice.agreement_id
                },
                'billing_period': {
                    'start': invoice.billing_period_start.isoformat(),
                    'end': invoice.billing_period_end.isoformat()
                },
                'line_items': [
                    {
                        'description': f"Investment Advisory Fee - {item.asset_class_name} Assets",
                        'asset_class': item.asset_class_name,
                        'portfolio_value': float(item.portfolio_value),
                        'rate': f"{float(item.rate_percentage) * 100:.2f}%",
                        'amount': float(item.final_fee),
                        'fee_breakdown': item.fee_breakdown
                    }
                    for item in invoice.line_items
                ],
                'totals': {
                    'subtotal': float(invoice.net_amount),
                    'tax_rate': float(invoice.tax_rate),
                    'tax_amount': float(invoice.tax_amount),
                    'total_amount': float(invoice.total_amount)
                },
                'status': invoice.status,
                'paid_date': invoice.paid_date.isoformat() if invoice.paid_date else None,
                'payment_reference': invoice.payment_reference,
                'notes': invoice.notes
            }
            
        except Exception as e:
            logger.error(f"Error getting invoice {invoice_id}: {str(e)}")
            return {'error': f'Invoice retrieval error: {str(e)}'}
    
    @classmethod
    def update_invoice_status(cls, invoice_id: int, status: str, 
                             payment_reference: str = None, 
                             payment_method: str = None) -> bool:
        """Update invoice status"""
        if not current_app:
            return False
            
        try:
            from app import db
            from models import Invoice
            
            invoice = Invoice.query.get(invoice_id)
            if not invoice:
                return False
                
            invoice.status = status
            
            if status == 'paid':
                invoice.paid_date = date.today()
                if payment_reference:
                    invoice.payment_reference = payment_reference
                if payment_method:
                    invoice.payment_method = payment_method
                    
            db.session.commit()
            return True
            
        except Exception as e:
            logger.error(f"Error updating invoice status: {str(e)}")
            from app import db
            db.session.rollback()
            return False
