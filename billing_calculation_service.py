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

            from services.agreement_billing_transition_service import (
                build_transition_billing_notes,
                format_valuation_date_rule_description,
                is_fixed_then_aua_agreement,
                resolve_effective_advisory_model,
                valuation_date_for_period,
            )

            advisory_model = resolve_effective_advisory_model(agreement, billing_date)

            # Get billing period
            billing_period = cls._get_billing_period(agreement, billing_date)
            if 'error' in billing_period:
                return billing_period

            tax_rate = cls._get_tax_rate()

            # ── Fixed-fee path (incl. first year of fixed_then_aua) ───────────
            if advisory_model == 'fixed_fee':
                result = cls._calculate_fixed_fee_billing(
                    agreement, client, billing_period, billing_date, tax_rate
                )
                if isinstance(result, dict) and 'error' not in result:
                    result['effective_advisory_model'] = advisory_model
                    if is_fixed_then_aua_agreement(agreement):
                        result['billing_transition'] = 'fixed_then_aua_year_1'
                        notes = list(result.get('billing_notes') or [])
                        notes.extend(build_transition_billing_notes(agreement, billing_date))
                        result['billing_notes'] = notes
                return result

            # ── AUA (percentage-based) path ───────────────────────────────────
            valuation_date = cls._resolve_valuation_date(agreement, billing_period, billing_date)
            vars_map = {v.variable_name: v.variable_value for v in (agreement.variables or [])}
            val_rule = vars_map.get('valuation_date_rule', 'prepaid')
            period_start_d = date.fromisoformat(billing_period['start'])
            period_end_d = date.fromisoformat(billing_period['end'])
            _, val_short = valuation_date_for_period(val_rule, period_start_d, period_end_d)

            # Get portfolio values by asset class as of valuation_date
            portfolio_data = cls._get_portfolio_values_by_asset_class(client, valuation_date)
            if 'error' in portfolio_data:
                return portfolio_data
                
            # Get billing rates for this agreement
            billing_rates = cls._get_billing_rates(agreement_id)
            if 'error' in billing_rates:
                return billing_rates
                
            # Calculate fees for each asset class.
            # Rate structures are stored as *annual* percentages; prorate by frequency.
            freq = billing_period.get('frequency', 'yearly')
            proration_factor = {'yearly': 1.0, 'half_yearly': 0.5, 'quarterly': 0.25}.get(freq, 1.0)
            billable_assets = cls._calculate_asset_class_fees(
                portfolio_data['asset_class_values'],
                billing_rates,
                proration_factor=proration_factor,
                frequency=freq,
            )
            
            # Calculate totals
            # billable_assets values are floats; compute totals in Decimal space
            subtotal = sum(Decimal(str(asset['final_fee'])) for asset in billable_assets)
            tax_amount = (subtotal * Decimal(str(tax_rate))).quantize(Decimal('0.01'))
            total_amount = (subtotal + tax_amount).quantize(Decimal('0.01'))
            
            transition_notes = build_transition_billing_notes(
                agreement,
                billing_date,
                valuation_date=valuation_date,
                valuation_label=val_short,
            )
            val_basis_desc = format_valuation_date_rule_description(
                val_rule,
                period_start_month=int(billing_period.get('period_start_month') or 1),
                frequency=billing_period.get('frequency', 'yearly'),
            )
            billing_notes = cls._generate_billing_notes(agreement, billable_assets)
            billing_notes = (transition_notes or []) + (billing_notes or [])

            return {
                'agreement_id': agreement_id,
                'client_id': client.id,
                'client_name': client.name,
                'billing_period': billing_period,
                'advisory_model': 'aua',
                'effective_advisory_model': advisory_model,
                'billing_transition': (
                    'fixed_then_aua_ongoing' if is_fixed_then_aua_agreement(agreement) else None
                ),
                'valuation_date_rule': val_rule,
                'valuation_date_basis': val_basis_desc,
                'portfolio_summary': {
                    'total_value': float(portfolio_data['total_value']),
                    'billable_value': sum(asset['portfolio_value'] for asset in billable_assets),
                    'excluded_assets': portfolio_data.get('excluded_assets', [])
                },
                'portfolio_snapshot': portfolio_data.get('portfolio_snapshot'),
                'billable_assets': billable_assets,
                'totals': {
                    'subtotal': float(subtotal),
                    'tax_rate': float(tax_rate),
                    'tax_amount': float(tax_amount),
                    'total_amount': float(total_amount)
                },
                'valuation_date': valuation_date.isoformat(),
                'billing_notes': billing_notes,
            }
            
        except Exception as e:
            logger.error(f"Error calculating billing for agreement {agreement_id}: {str(e)}")
            return {'error': f'Calculation error: {str(e)}'}
    
    @classmethod
    def _calculate_fixed_fee_billing(
        cls,
        agreement: Agreement,
        client,
        billing_period: Dict,
        billing_date: date,
        tax_rate: float,
    ) -> Dict:
        """
        Calculate billing for a fixed-fee agreement.

        Escalation rule:
          - Base annual fee = fixed_annual_fee stored in AgreementVariables.
          - Escalation applies once per *completed* year from agreement start date.
          - Years completed = floor((billing_date - start_date).days / 365)
          - Current annual fee = base_fee * (1 + esc_pct/100) ^ years_completed
          - Period fee = current_annual_fee / frequency_divisor
            where divisor: yearly=1, half_yearly=2, quarterly=4
        """
        # Read fixed-fee config from agreement variables
        from services.agreement_billing_transition_service import (
            first_year_billing_frequency,
            first_year_fixed_annual_fee,
            is_fixed_then_aua_agreement,
            years_since_billing_start,
        )

        vars_map = {v.variable_name: v.variable_value for v in (agreement.variables or [])}
        raw_fee = vars_map.get('fixed_annual_fee', '')
        raw_esc = vars_map.get('fixed_fee_escalation_pct', '0')
        freq_override = None

        if is_fixed_then_aua_agreement(agreement) and years_since_billing_start(agreement, billing_date) < 1:
            fy_fee = first_year_fixed_annual_fee(agreement)
            if fy_fee is None and not raw_fee:
                return {'error': 'First-year fixed fee not configured for this agreement'}
            base_annual_fee = fy_fee if fy_fee is not None else Decimal(raw_fee)
            escalation_pct = Decimal('0')
            freq_override = first_year_billing_frequency(agreement)
        else:
            if not raw_fee:
                return {'error': 'Fixed annual fee not configured for this agreement'}
            try:
                base_annual_fee = Decimal(raw_fee)
            except Exception:
                return {'error': f'Invalid fixed_annual_fee value: {raw_fee!r}'}
            try:
                escalation_pct = Decimal(raw_esc) if raw_esc else Decimal('0')
            except Exception:
                escalation_pct = Decimal('0')

        # Determine start date priority:
        #  1. billing_start_date AgreementVariable (set from form)
        #  2. Active BillingSchedule.billing_start_date
        #  3. agreement.signed_date
        start_date = None
        start_var = vars_map.get('billing_start_date', '')
        if start_var:
            try:
                start_date = date.fromisoformat(start_var)
            except ValueError:
                pass
        if not start_date:
            schedule = next(
                (s for s in getattr(agreement, 'billing_schedules', []) if s.is_active),
                None
            )
            start_date = schedule.billing_start_date if schedule else None
        if not start_date:
            raw = getattr(agreement, 'signed_date', None)
            if raw is not None:
                # signed_date is stored as DateTime on some environments
                start_date = raw.date() if hasattr(raw, 'date') else raw

        # Years completed since agreement start (whole years only)
        years_completed = 0
        if start_date and billing_date > start_date:
            years_completed = (billing_date - start_date).days // 365

        # Current annual fee after escalation
        if escalation_pct > 0 and years_completed > 0:
            current_annual_fee = base_annual_fee * (
                (Decimal('1') + escalation_pct / Decimal('100')) ** years_completed
            )
            current_annual_fee = current_annual_fee.quantize(Decimal('0.01'))
        else:
            current_annual_fee = base_annual_fee

        # Prorate by billing frequency (first year of fixed_then_aua may use its own frequency)
        freq = freq_override or billing_period.get('frequency', 'yearly')
        divisors = {'yearly': 1, 'half_yearly': 2, 'quarterly': 4}
        divisor = divisors.get(freq, 1)
        period_fee = (current_annual_fee / Decimal(str(divisor))).quantize(Decimal('0.01'))

        tax_amount    = (period_fee * Decimal(str(tax_rate))).quantize(Decimal('0.01'))
        total_amount  = period_fee + tax_amount

        notes = []
        if years_completed > 0 and escalation_pct > 0:
            notes.append(
                f'Escalation of {escalation_pct}% applied for year {years_completed + 1} '
                f'(base: ₹{base_annual_fee:,.2f} → current: ₹{current_annual_fee:,.2f} p.a.)'
            )
        if divisor > 1:
            notes.append(
                f'{freq.replace("_", "-").title()} billing: '
                f'₹{current_annual_fee:,.2f} ÷ {divisor} = ₹{period_fee:,.2f} this period'
            )

        return {
            'agreement_id': agreement.id,
            'client_id': client.id,
            'client_name': client.name,
            'billing_period': billing_period,
            'advisory_model': 'fixed_fee',
            'fixed_fee_details': {
                'base_annual_fee': float(base_annual_fee),
                'escalation_pct': float(escalation_pct),
                'years_completed': years_completed,
                'current_annual_fee': float(current_annual_fee),
                'billing_frequency': freq,
                'period_fee': float(period_fee),
            },
            'totals': {
                'subtotal': float(period_fee),
                'tax_rate': float(tax_rate),
                'tax_amount': float(tax_amount),
                'total_amount': float(total_amount),
            },
            'billing_notes': notes,
        }

    @classmethod
    def _resolve_valuation_date(cls, agreement: Agreement, billing_period: Dict, billing_date: date) -> date:
        """
        Return the date whose portfolio value is used for AUA fee calculation.

        Two billing basis options (stored as AgreementVariable 'valuation_date_rule'):

          prepaid   — Pre-paid / Advance billing.
                      AUA is valued at the END OF THE PREVIOUS PERIOD (last day before
                      this period starts).  For Jan–Jun half-year: 31 Dec.
                      For Jul–Dec half-year: 30 Jun.

          postpaid  — Post-paid / Arrears billing.
                      AUA is valued at the END OF THE CURRENT PERIOD (last day of
                      this period).  For Jan–Jun half-year: 30 Jun.
                      For Jul–Dec half-year: 31 Dec.

        Old agreements without the variable fall back to prepaid behaviour.
        """
        vars_map = {v.variable_name: v.variable_value for v in (agreement.variables or [])}
        rule = vars_map.get('valuation_date_rule', 'prepaid')
        if rule in ('day_before_period_start', 'prepaid'):
            rule = 'prepaid'

        if rule == 'postpaid':
            return date.fromisoformat(billing_period['end'])
        else:
            # prepaid (default) — last day of previous period
            period_start = date.fromisoformat(billing_period['start'])
            return period_start - timedelta(days=1)

    @classmethod
    def _get_billing_period(cls, agreement: Agreement, billing_date: date) -> Dict:
        """
        Get billing period for the given date, anchored to period_start_month.

        period_start_month (1–12, stored in AgreementVariables) defines when
        each billing year starts.  All period boundaries roll from that month.

        Examples with period_start_month=4 (April):
          yearly     : Apr 1 – Mar 31 (next year)
          half_yearly: Apr 1 – Sep 30  |  Oct 1 – Mar 31
          quarterly  : Apr 1 – Jun 30  |  Jul 1 – Sep 30  |  Oct 1 – Dec 31  |  Jan 1 – Mar 31

        Legacy behaviour: period_start_month defaults to 1 (January), which
        reproduces the old hardcoded Jan–Jun / Jul–Dec logic exactly.
        """
        try:
            vars_map = {v.variable_name: v.variable_value for v in (agreement.variables or [])}

            billing_frequency = vars_map.get('billing_frequency', 'yearly')

            try:
                anchor_month = int(vars_map.get('period_start_month', '1'))
                if anchor_month < 1 or anchor_month > 12:
                    anchor_month = 1
            except (ValueError, TypeError):
                anchor_month = 1

            period_start, period_end = cls._period_boundaries(
                billing_date, billing_frequency, anchor_month
            )

            return {
                'start': period_start.isoformat(),
                'end': period_end.isoformat(),
                'frequency': billing_frequency,
                'period_start_month': anchor_month,
            }

        except Exception as e:
            logger.error(f"Error calculating billing period: {str(e)}")
            return {'error': f'Billing period calculation error: {str(e)}'}

    @staticmethod
    def _period_boundaries(billing_date: date, frequency: str, anchor_month: int):
        """
        Return (period_start, period_end) dates for the period that contains
        billing_date, given an anchor_month and billing frequency.

        Works by counting calendar months from the anchor, modulo the period
        length, to find the start of the current period.  Handles year wrap
        transparently (e.g. anchor_month=10, quarterly → Oct-Dec / Jan-Mar / …).
        """
        from calendar import monthrange

        period_months = {'yearly': 12, 'half_yearly': 6, 'quarterly': 3}.get(frequency, 12)

        # Months elapsed since anchor in absolute month units
        abs_billing = billing_date.year * 12 + (billing_date.month - 1)
        abs_anchor  = billing_date.year * 12 + (anchor_month - 1)

        # Adjust anchor backwards if it's ahead of billing_date within same year
        if abs_anchor > abs_billing:
            abs_anchor -= 12

        offset_months = (abs_billing - abs_anchor) % period_months
        abs_start = abs_billing - offset_months

        start_year  = abs_start // 12
        start_month = abs_start % 12 + 1
        period_start = date(start_year, start_month, 1)

        # End = start + period_months months - 1 day
        abs_end = abs_start + period_months
        end_year  = abs_end // 12
        end_month = abs_end % 12 + 1
        _, last_day = monthrange(end_year, end_month)
        period_end = date(end_year, end_month, last_day) - timedelta(days=1)
        # period_end is the last day of the month BEFORE the next period start
        # i.e. one day before the next period start
        abs_next_start = abs_start + period_months
        next_year  = abs_next_start // 12
        next_month = abs_next_start % 12 + 1
        period_end = date(next_year, next_month, 1) - timedelta(days=1)

        return period_start, period_end
    
    @classmethod
    def _get_portfolio_values_by_asset_class(cls, client: Client, valuation_date: date) -> Dict:
        """
        Get portfolio values grouped by asset class **as-of** valuation_date.

        Important: we intentionally do NOT use current holdings + historical prices,
        because quantities also change over time. Instead, we use the portfolio
        reconstruction stack (portfolio construction / forward holding calc) to
        get holdings and values as-of the requested date.
        """
        try:
            asset_class_values = {}
            total_value = Decimal('0.0')
            excluded_assets = []

            # Portfolio construction module (as-of snapshot)
            from services.forward_holding_calculation_service import get_client_portfolio_by_date
            from models import AssetClass

            portfolio = get_client_portfolio_by_date(client.id, valuation_date) or {}
            holdings = portfolio.get("holdings") or []
            _asset_class_id_cache = {}

            from services.holding_advisory_scope_service import (
                SCOPE_BILLING,
                filter_portfolio_holdings,
                get_excluded_security_ids,
                sum_holding_values,
            )

            excluded_ids = get_excluded_security_ids(client.id, SCOPE_BILLING)
            billable_holdings, client_excluded_holdings = filter_portfolio_holdings(
                holdings, excluded_ids
            )
            for h in client_excluded_holdings:
                sym = (h.get("symbol") or h.get("security_symbol") or "").strip()
                if sym:
                    excluded_assets.append(f"{sym} (advisory scope)")

            for h in billable_holdings:
                asset_class_name = (h.get("asset_class") or "").strip() or "Unknown"
                raw_acid = h.get("asset_class_id")  # may be absent in some payloads
                value_rs = Decimal(str(h.get("current_value") or h.get("value") or 0.0))

                if asset_class_name == "Unknown":
                    sym = (h.get("symbol") or h.get("security_symbol") or "").strip()
                    if sym:
                        excluded_assets.append(sym)
                    continue

                # Ensure we have a DB AssetClass id for invoice line-items (NOT NULL FK).
                asset_class_id = None
                if str(raw_acid or "").isdigit():
                    asset_class_id = int(raw_acid)
                else:
                    if asset_class_name in _asset_class_id_cache:
                        asset_class_id = _asset_class_id_cache[asset_class_name]
                    else:
                        ac = AssetClass.query.filter_by(name=asset_class_name).first()
                        asset_class_id = ac.id if ac else None
                        _asset_class_id_cache[asset_class_name] = asset_class_id

                if asset_class_name not in asset_class_values:
                    asset_class_values[asset_class_name] = {
                        'asset_class_id': asset_class_id,
                        'asset_class_name': asset_class_name,
                        'portfolio_value': Decimal('0.0')
                    }

                asset_class_values[asset_class_name]['portfolio_value'] += value_rs
                total_value += value_rs
                
            return {
                'asset_class_values': asset_class_values,
                'total_value': total_value,
                'excluded_assets': excluded_assets,
                # Persistable snapshot payload for invoices/PDFs
                'portfolio_snapshot': {
                    'as_of': valuation_date.isoformat(),
                    'total_value': float(total_value),
                    'advisory_aua_value': float(total_value),
                    'excluded_from_advisory_aua_value': sum_holding_values(client_excluded_holdings),
                    'holdings': holdings,
                    'excluded_holdings': [
                        {
                            'security_id': h.get('security_id'),
                            'symbol': h.get('symbol'),
                            'value': float(h.get('current_value') or h.get('value') or 0),
                            'reason': 'client_advisory_scope',
                        }
                        for h in client_excluded_holdings
                    ],
                },
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
    def _calculate_asset_class_fees(
        cls,
        asset_class_values: Dict,
        billing_rates: Dict,
        proration_factor: float = 1.0,
        frequency: str = 'yearly',
    ) -> List[Dict]:
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
                
            # Calculate fee (annual rate prorated by billing frequency)
            annual_rate = applicable_rate['rate_percentage']
            calculated_fee = portfolio_value * annual_rate * proration_factor
            
            # Apply min/max fee constraints
            min_fee = applicable_rate['min_fee']
            max_fee = applicable_rate['max_fee']
            
            final_fee = calculated_fee
            if proration_factor != 1.0:
                fee_breakdown = f"Annual rate prorated for {frequency.replace('_','-')} period"
            else:
                fee_breakdown = "Standard annual rate applied"
            
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
                'rate_percentage': annual_rate,
                'calculated_fee': calculated_fee,
                'proration_factor': proration_factor,
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

            # Advance by calendar months — same logic as _next_billing_date_from_start
            months_map = {'quarterly': 3, 'half_yearly': 6, 'yearly': 12}
            months = months_map.get(frequency, 12)
            new_month = invoice_date.month + months
            new_year  = invoice_date.year + (new_month - 1) // 12
            new_month = (new_month - 1) % 12 + 1
            import calendar as _cal
            last_day = _cal.monthrange(new_year, new_month)[1]
            next_date = invoice_date.replace(
                year=new_year,
                month=new_month,
                day=min(invoice_date.day, last_day),
            )

            schedule.next_billing_date = next_date
            schedule.billing_cycle_number += 1

            from extensions import db
            db.session.commit()
            return True

        except Exception as e:
            logger.error(f"Error updating billing schedule: {str(e)}")
            from extensions import db
            db.session.rollback()
            return False










