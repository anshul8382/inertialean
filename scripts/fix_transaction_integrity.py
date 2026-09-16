#!/usr/bin/env python3
"""
Fix transaction data integrity issues
- Fix null created_at fields
- Ensure transaction_date and created_at are properly set
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import create_app
from models import db, Transaction
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def fix_transaction_created_at():
    """Fix transactions where created_at is null"""
    app = create_app()
    
    with app.app_context():
        logger.info("=== Fixing Transaction created_at Field ===")
        
        # Find transactions with null created_at
        null_created_at_transactions = Transaction.query.filter(
            Transaction.created_at.is_(None)
        ).all()
        
        logger.info(f"Found {len(null_created_at_transactions)} transactions with null created_at")
        
        if null_created_at_transactions:
            try:
                for transaction in null_created_at_transactions:
                    # Set created_at to transaction_date if available, otherwise current time
                    if transaction.transaction_date:
                        transaction.created_at = transaction.transaction_date
                    else:
                        transaction.created_at = datetime.utcnow()
                    logger.debug(f"Fixed transaction {transaction.id}")
                
                db.session.commit()
                logger.info(f"✅ Fixed {len(null_created_at_transactions)} transactions")
                
            except Exception as e:
                db.session.rollback()
                logger.error(f"❌ Error fixing transactions: {str(e)}")
                return False
        else:
            logger.info("✅ No transactions with null created_at found")
        
        return True


def validate_transaction_data():
    """Validate transaction data integrity"""
    app = create_app()
    
    with app.app_context():
        logger.info("=== Validating Transaction Data ===")
        
        # Check for transactions with null values in critical fields
        issues = []
        
        # Check for null transaction_date
        null_date_transactions = Transaction.query.filter(
            Transaction.transaction_date.is_(None)
        ).count()
        if null_date_transactions > 0:
            issues.append(f"{null_date_transactions} transactions with null transaction_date")
        
        # Check for null created_at
        null_created_transactions = Transaction.query.filter(
            Transaction.created_at.is_(None)
        ).count()
        if null_created_transactions > 0:
            issues.append(f"{null_created_transactions} transactions with null created_at")
        
        # Check for transactions with zero or negative amounts for non-SPLIT/BONUS transactions
        invalid_amount_transactions = Transaction.query.filter(
            db.and_(
                ~Transaction.type.in_(['SPLIT', 'BONUS']),
                db.or_(
                    Transaction.amount == 0,
                    Transaction.amount < 0
                )
            )
        ).count()
        if invalid_amount_transactions > 0:
            issues.append(f"{invalid_amount_transactions} transactions with invalid amounts")
        
        if issues:
            logger.warning("⚠️ Found data integrity issues:")
            for issue in issues:
                logger.warning(f"  - {issue}")
            return False
        else:
            logger.info("✅ No data integrity issues found")
            return True


if __name__ == "__main__":
    logger.info("Starting transaction data integrity fix...")
    
    # First validate
    if not validate_transaction_data():
        logger.info("Found issues, attempting to fix...")
    
    # Then fix
    if fix_transaction_created_at():
        logger.info("✅ Transaction integrity fix completed successfully")
    else:
        logger.error("❌ Transaction integrity fix failed")
        sys.exit(1)

