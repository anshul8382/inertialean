#!/usr/bin/env python3
"""
Data Validation Rules for Corporate Actions
Prevents manual entry problems and ensures data integrity
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import create_app
from models import db, Client, Security, Transaction, Holding, CorporateAction
from datetime import datetime, date, timedelta
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CorporateActionValidator:
    """Validates corporate actions before entry"""
    
    STANDARD_SPLIT_RATIOS = [1.5, 2.0, 3.0, 5.0, 10.0]
    MAX_SPLIT_RATIO = 10.0
    MAX_TOTAL_SPLIT_RATIO = 20.0
    MIN_DAYS_BETWEEN_ACTIONS = 30
    
    @staticmethod
    def validate_split_ratio(ratio):
        """Validate split ratio"""
        if ratio <= 1.0:
            return False, "Split ratio must be greater than 1.0"
        
        if ratio > CorporateActionValidator.MAX_SPLIT_RATIO:
            return False, f"Split ratio cannot exceed {CorporateActionValidator.MAX_SPLIT_RATIO}:1"
        
        if ratio not in CorporateActionValidator.STANDARD_SPLIT_RATIOS:
            return False, f"Non-standard split ratio. Standard ratios: {CorporateActionValidator.STANDARD_SPLIT_RATIOS}"
        
        return True, "Valid split ratio"
    
    @staticmethod
    def validate_bonus_ratio(ratio):
        """Validate bonus ratio"""
        if ratio <= 0.0:
            return False, "Bonus ratio must be greater than 0.0"
        
        if ratio > 2.0:
            return False, "Bonus ratio cannot exceed 2.0"
        
        return True, "Valid bonus ratio"
    
    @staticmethod
    def check_duplicate_actions(security_id, action_date, action_type):
        """Check for duplicate corporate actions"""
        app = create_app()
        with app.app_context():
            # Check for same action on same date
            existing = CorporateAction.query.filter(
                CorporateAction.security_id == security_id,
                CorporateAction.action_date == action_date,
                CorporateAction.action_type == action_type
            ).first()
            
            if existing:
                return False, f"Duplicate {action_type} action already exists on {action_date}"
            
            # Check for actions within minimum days
            recent_actions = CorporateAction.query.filter(
                CorporateAction.security_id == security_id,
                CorporateAction.action_date >= action_date - timedelta(days=CorporateActionValidator.MIN_DAYS_BETWEEN_ACTIONS),
                CorporateAction.action_date <= action_date + timedelta(days=CorporateActionValidator.MIN_DAYS_BETWEEN_ACTIONS)
            ).all()
            
            if recent_actions:
                return False, f"Corporate action within {CorporateActionValidator.MIN_DAYS_BETWEEN_ACTIONS} days of existing actions"
            
            return True, "No duplicate actions found"
    
    @staticmethod
    def validate_total_split_ratio(security_id, new_ratio):
        """Validate total split ratio doesn't exceed maximum"""
        app = create_app()
        with app.app_context():
            existing_actions = CorporateAction.query.filter(
                CorporateAction.security_id == security_id,
                CorporateAction.action_type == 'SPLIT'
            ).all()
            
            total_ratio = 1.0
            for action in existing_actions:
                total_ratio *= float(action.ratio)
            
            total_ratio *= new_ratio
            
            if total_ratio > CorporateActionValidator.MAX_TOTAL_SPLIT_RATIO:
                return False, f"Total split ratio would exceed {CorporateActionValidator.MAX_TOTAL_SPLIT_RATIO}:1"
            
            return True, f"Total split ratio: {total_ratio}:1"
    
    @staticmethod
    def validate_corporate_action(security_id, action_type, action_date, ratio):
        """Comprehensive validation of corporate action"""
        validations = []
        
        # Validate ratio
        if action_type == 'SPLIT':
            is_valid, message = CorporateActionValidator.validate_split_ratio(ratio)
        elif action_type == 'BONUS':
            is_valid, message = CorporateActionValidator.validate_bonus_ratio(ratio)
        else:
            is_valid, message = False, f"Unknown action type: {action_type}"
        
        validations.append(('ratio', is_valid, message))
        
        # Check for duplicates
        is_valid, message = CorporateActionValidator.check_duplicate_actions(security_id, action_date, action_type)
        validations.append(('duplicate', is_valid, message))
        
        # Validate total split ratio for splits
        if action_type == 'SPLIT':
            is_valid, message = CorporateActionValidator.validate_total_split_ratio(security_id, ratio)
            validations.append(('total_ratio', is_valid, message))
        
        # Check if all validations pass
        all_valid = all(v[1] for v in validations)
        
        return all_valid, validations

class HoldingValidator:
    """Validates holdings against transactions and corporate actions"""
    
    MAX_QUANTITY_DISCREPANCY_PERCENT = 10.0
    MAX_PRICE_DISCREPANCY_PERCENT = 20.0
    
    @staticmethod
    def validate_holding_quantity(client_id, security_id, holding_quantity):
        """Validate holding quantity against transactions and corporate actions"""
        app = create_app()
        with app.app_context():
            # Get BUY transactions
            buy_transactions = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.security_id == security_id,
                Transaction.type == 'BUY'
            ).all()
            
            if not buy_transactions:
                return True, "No transactions to validate against"
            
            # Calculate expected quantity
            transaction_quantity = sum(float(tx.quantity) for tx in buy_transactions)
            
            # Apply corporate actions
            corporate_actions = CorporateAction.query.filter_by(security_id=security_id).all()
            expected_quantity = transaction_quantity
            
            for action in corporate_actions:
                if action.action_type == 'SPLIT':
                    expected_quantity *= float(action.ratio)
                elif action.action_type == 'BONUS':
                    expected_quantity *= (1.0 + float(action.ratio))
            
            # Check discrepancy
            discrepancy = abs(holding_quantity - expected_quantity)
            discrepancy_percent = (discrepancy / expected_quantity * 100) if expected_quantity > 0 else 0
            
            if discrepancy_percent > HoldingValidator.MAX_QUANTITY_DISCREPANCY_PERCENT:
                return False, f"Quantity discrepancy: {discrepancy_percent:.1f}% (expected: {expected_quantity:.2f}, actual: {holding_quantity:.2f})"
            
            return True, f"Quantity validation passed (discrepancy: {discrepancy_percent:.1f}%)"
    
    @staticmethod
    def validate_holding_price(client_id, security_id, holding_avg_price):
        """Validate holding average price against transactions"""
        app = create_app()
        with app.app_context():
            # Get BUY transactions
            buy_transactions = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.security_id == security_id,
                Transaction.type == 'BUY'
            ).all()
            
            if not buy_transactions:
                return True, "No transactions to validate against"
            
            # Calculate expected average price
            total_cost = sum(float(tx.quantity) * float(tx.price) for tx in buy_transactions)
            total_quantity = sum(float(tx.quantity) for tx in buy_transactions)
            
            if total_quantity == 0:
                return True, "No quantity to calculate average price"
            
            expected_avg_price = total_cost / total_quantity
            
            # Check discrepancy
            discrepancy = abs(holding_avg_price - expected_avg_price)
            discrepancy_percent = (discrepancy / expected_avg_price * 100) if expected_avg_price > 0 else 0
            
            if discrepancy_percent > HoldingValidator.MAX_PRICE_DISCREPANCY_PERCENT:
                return False, f"Price discrepancy: {discrepancy_percent:.1f}% (expected: ₹{expected_avg_price:.2f}, actual: ₹{holding_avg_price:.2f})"
            
            return True, f"Price validation passed (discrepancy: {discrepancy_percent:.1f}%)"

class PnLValidator:
    """Validates P&L calculations for anomalies"""
    
    MAX_PROFIT_PERCENT = 200.0
    MAX_LOSS_PERCENT = -50.0
    
    @staticmethod
    def validate_pnl_percentage(pnl_percent):
        """Validate P&L percentage for anomalies"""
        if pnl_percent > PnLValidator.MAX_PROFIT_PERCENT:
            return False, f"Excessive profit: {pnl_percent:.1f}% (max: {PnLValidator.MAX_PROFIT_PERCENT}%)"
        
        if pnl_percent < PnLValidator.MAX_LOSS_PERCENT:
            return False, f"Excessive loss: {pnl_percent:.1f}% (min: {PnLValidator.MAX_LOSS_PERCENT}%)"
        
        return True, f"P&L validation passed: {pnl_percent:.1f}%"
    
    @staticmethod
    def validate_holding_pnl(client_id, security_id):
        """Validate P&L for a specific holding"""
        app = create_app()
        with app.app_context():
            holding = Holding.query.filter_by(client_id=client_id, security_id=security_id).first()
            security = Security.query.get(security_id)
            
            if not holding or not security or not security.current_price:
                return True, "Cannot validate P&L - missing data"
            
            # Calculate P&L
            current_value = float(holding.quantity) * float(security.current_price)
            cost_basis = float(holding.quantity) * float(holding.average_price)
            pnl = current_value - cost_basis
            pnl_percent = (pnl / cost_basis * 100) if cost_basis > 0 else 0
            
            return PnLValidator.validate_pnl_percentage(pnl_percent)

def main():
    """Main function to demonstrate validation rules"""
    print("🔍 Data Validation Rules for Corporate Actions")
    print("=" * 50)
    
    print(f"\n📊 Validation Rules:")
    print(f"1. Split Ratios: Must be one of {CorporateActionValidator.STANDARD_SPLIT_RATIOS}")
    print(f"2. Maximum Split Ratio: {CorporateActionValidator.MAX_SPLIT_RATIO}:1")
    print(f"3. Maximum Total Split Ratio: {CorporateActionValidator.MAX_TOTAL_SPLIT_RATIO}:1")
    print(f"4. Minimum Days Between Actions: {CorporateActionValidator.MIN_DAYS_BETWEEN_ACTIONS}")
    print(f"5. Maximum Quantity Discrepancy: {HoldingValidator.MAX_QUANTITY_DISCREPANCY_PERCENT}%")
    print(f"6. Maximum Price Discrepancy: {HoldingValidator.MAX_PRICE_DISCREPANCY_PERCENT}%")
    print(f"7. Maximum Profit Percentage: {PnLValidator.MAX_PROFIT_PERCENT}%")
    print(f"8. Maximum Loss Percentage: {PnLValidator.MAX_LOSS_PERCENT}%")
    
    print(f"\n🎯 Implementation:")
    print(f"1. Add these validations to corporate action entry forms")
    print(f"2. Implement database constraints")
    print(f"3. Create automated monitoring scripts")
    print(f"4. Set up alerts for validation failures")
    print(f"5. Regular reconciliation and validation")
    
    print(f"\n✅ Benefits:")
    print(f"1. Prevents manual entry errors")
    print(f"2. Ensures data integrity")
    print(f"3. Detects anomalies early")
    print(f"4. Reduces reconciliation issues")
    print(f"5. Improves system reliability")

if __name__ == "__main__":
    main()





